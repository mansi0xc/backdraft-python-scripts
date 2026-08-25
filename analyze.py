"""
Score each candidate reference-price method against Binance.

    python analyze.py

Reads data/*.csv, writes out/results.csv, out/error_table.md, out/*.png.

The question being answered:
"If Backdraft had used method X at every block in this window, how wrong
 would it have been, and how fast would it have reacted?"
"""

import math
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import config as C


# ------------------------------------------------------------------ tick <-> price
#
# v3 stores price as token1/token0 in RAW units:  1.0001^tick
# For USDC(6dp)/WETH(18dp):  human ETH-per-USDC = 1.0001^tick * 10^(dec0-dec1)
# so USDC-per-ETH = 10^(dec1-dec0) / 1.0001^tick
#
# Sanity: ETH at $3000 -> tick ~= 196260

LOG_10001 = math.log(1.0001)
SCALE = 10 ** (C.DEC1 - C.DEC0)     # 10^12


def tick_to_price(tick):
    """v3 tick -> USDC per ETH."""
    return SCALE / np.power(1.0001, tick)


def price_to_tick(price):
    """USDC per ETH -> equivalent v3 tick."""
    return np.log(SCALE / np.asarray(price, dtype=float)) / LOG_10001


# ------------------------------------------------------------------ series building

def pool_series(label, block_times):
    """
    Per-block tick and liquidity for one pool, forward-filled between swaps.

    A pool's price only changes when someone swaps, so the last swap's tick
    is the pool's price until the next one. Forward-fill is exactly right here.
    """
    df = pd.read_csv(os.path.join(C.DATA_DIR, f"swaps_{label}.csv"))
    if df.empty:
        return None

    # last swap in each block wins
    last = df.groupby("block").last().reset_index()[["block", "tick", "liquidity"]]

    s = block_times.merge(last, on="block", how="left")
    s["tick"] = s["tick"].ffill().bfill()
    s["liquidity"] = s["liquidity"].ffill().bfill()
    s = s.rename(columns={"tick": f"tick_{label}", "liquidity": f"liq_{label}"})
    return s[["block", "timestamp", f"tick_{label}", f"liq_{label}"]]


def time_weighted_average(ts, ticks, window_s):
    """
    Reconstruct what v3's observe() would return: a time-weighted mean tick
    over `window_s` seconds.

    v3 accumulates tick*seconds and divides the difference by elapsed time.
    Since our series is one row per block at ~12s spacing, a rolling mean over
    the equivalent number of blocks is the same computation.
    """
    dt = np.median(np.diff(ts)) if len(ts) > 1 else 12.0
    n = max(1, int(round(window_s / dt)))
    return pd.Series(ticks).rolling(n, min_periods=1).mean().to_numpy()


def weighted_median(values, weights):
    """
    Liquidity-weighted median across pools, row by row.

    Why median and not mean: a weighted mean moves in proportion to any
    manipulated source's weight. A weighted median doesn't move at all until
    an attacker controls >50% of total weight.
    """
    values = np.asarray(values, dtype=float)    # (n_rows, n_pools)
    weights = np.asarray(weights, dtype=float)
    out = np.empty(len(values))

    for i in range(len(values)):
        v, w = values[i], weights[i]
        good = ~np.isnan(v) & ~np.isnan(w) & (w > 0)
        if good.sum() == 0:
            out[i] = np.nan
            continue
        v, w = v[good], w[good]
        order = np.argsort(v)
        v, w = v[order], w[order]
        cw = np.cumsum(w) / w.sum()
        out[i] = v[np.searchsorted(cw, 0.5)]
    return out


def ema_series(ticks, alpha_bps, flow_rate=1.0, seed=0):
    """
    The broken baseline: an EMA of our own pool's tick history.

    flow_rate models thin flow. A pool's EMA can only update when a swap
    happens; on a new pool most blocks contain no swap, so the reference
    goes stale between them. flow_rate=0.1 means a swap in 1 block out of 10.
    """
    a = alpha_bps / 10_000.0
    rng = np.random.default_rng(seed)
    has_swap = rng.random(len(ticks)) < flow_rate
    has_swap[0] = True

    out = np.empty(len(ticks))
    acc = float(ticks[0])
    for i, t in enumerate(ticks):
        if has_swap[i]:
            acc += (float(t) - acc) * a
        out[i] = acc          # between swaps the reference is simply stale
    return out


# ------------------------------------------------------------------ main

def main():
    os.makedirs(C.OUT_DIR, exist_ok=True)

    block_times = pd.read_csv(os.path.join(C.DATA_DIR, "block_times.csv"))

    # ---- assemble per-pool series
    frame = block_times.copy()
    labels = []
    for pool in C.POOLS:
        s = pool_series(pool["label"], block_times)
        if s is None:
            print(f"skipping {pool['label']} (no data)")
            continue
        labels.append(pool["label"])
        frame = frame.merge(s.drop(columns=["timestamp"]), on="block", how="left")

    if not labels:
        raise SystemExit("No pool data. Run fetch.py first.")

    # ---- ground truth: Binance, forward-filled onto blocks
    binance = pd.read_csv(os.path.join(C.DATA_DIR, "binance.csv")).sort_values("timestamp")
    frame = pd.merge_asof(
        frame.sort_values("timestamp"),
        binance[["timestamp", "close"]].rename(columns={"close": "cex_price"}),
        on="timestamp", direction="backward",
    )
    frame["cex_tick"] = price_to_tick(frame["cex_price"])
    frame = frame.dropna(subset=["cex_tick"]).reset_index(drop=True)

    ts = frame["timestamp"].to_numpy()
    tick_cols = [f"tick_{l}" for l in labels]
    liq_cols  = [f"liq_{l}"  for l in labels]
    ticks_all = frame[tick_cols].to_numpy()
    liq_all   = frame[liq_cols].to_numpy()

    # ---- candidate methods, each producing a tick estimate per block
    methods = {}

    tgt = C.TARGET_POOL if f"tick_{C.TARGET_POOL}" in frame else labels[0]
    tgt_ticks = frame[f"tick_{tgt}"].to_numpy()
    for fr in C.EMA_FLOW_RATES:
        name = "own_pool_ema" if fr >= 1.0 else f"own_pool_ema_flow{fr:g}"
        methods[name] = ema_series(tgt_ticks, C.EMA_ALPHA_BPS, flow_rate=fr)

    deepest = labels[int(np.nanargmax(np.nanmedian(liq_all, axis=0)))]
    methods[f"spot_{deepest}"] = frame[f"tick_{deepest}"].to_numpy()

    for w in C.TWAP_WINDOWS:
        methods[f"twap_{w}s_{deepest}"] = time_weighted_average(
            ts, frame[f"tick_{deepest}"].to_numpy(), w)

    wsum = np.nansum(liq_all, axis=1, keepdims=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        methods["composite_mean"] = np.nansum(ticks_all * liq_all, axis=1) / wsum.ravel()

    methods["composite_median"] = weighted_median(ticks_all, liq_all)

    # guarded: freeze when composite spot and composite TWAP disagree
    comp_twap = time_weighted_average(ts, methods["composite_median"], max(C.TWAP_WINDOWS))
    dev = np.abs(methods["composite_median"] - comp_twap)
    guarded = methods["composite_median"].copy()
    frozen = dev > C.GUARD_MAX_DEV_TICKS
    guarded[frozen] = np.nan
    methods["composite_median_guarded"] = guarded
    freeze_rate = float(np.mean(frozen))

    # ---- score every method
    cex_tick = frame["cex_tick"].to_numpy()
    results = []

    for name, est in methods.items():
        err_ticks = est - cex_tick               # 1 tick ~= 1 basis point
        valid = ~np.isnan(err_ticks)
        e = np.abs(err_ticks[valid])
        if len(e) == 0:
            continue

        # reaction lag: cross-correlate estimate changes against truth changes
        lag = np.nan
        d_est = np.diff(np.nan_to_num(est, nan=np.nanmean(est)))
        d_cex = np.diff(cex_tick)
        if d_cex.std() > 0 and d_est.std() > 0:
            cors = []
            for k in range(0, 26):
                a = d_est[k:] if k else d_est
                b = d_cex[:len(d_cex) - k] if k else d_cex
                n = min(len(a), len(b))
                if n > 10:
                    cors.append(np.corrcoef(a[:n], b[:n])[0, 1])
            if cors:
                lag = int(np.nanargmax(cors))

        results.append({
            "method":        name,
            "mean_err_bps":  round(float(e.mean()), 2),
            "median_err_bps": round(float(np.median(e)), 2),
            "p95_err_bps":   round(float(np.percentile(e, 95)), 2),
            "max_err_bps":   round(float(e.max()), 2),
            "lag_blocks":    lag,
            "coverage_pct":  round(100 * float(valid.mean()), 1),
        })

    res = pd.DataFrame(results).sort_values("mean_err_bps").reset_index(drop=True)
    res.to_csv(os.path.join(C.OUT_DIR, "results.csv"), index=False)

    # ---- markdown table for the README
    md = ["| Method | Mean err (bps) | Median | p95 | Max | Lag (blocks) | Coverage |",
          "|---|---|---|---|---|---|---|"]
    for _, r in res.iterrows():
        md.append(f"| `{r['method']}` | {r['mean_err_bps']} | {r['median_err_bps']} | "
                  f"{r['p95_err_bps']} | {r['max_err_bps']} | {r['lag_blocks']} | {r['coverage_pct']}% |")
    md.append("")
    md.append(f"Guard freeze rate: {freeze_rate*100:.2f}% of blocks "
              f"(threshold {C.GUARD_MAX_DEV_TICKS} ticks)")
    md.append(f"Window: blocks {frame.block.min()}–{frame.block.max()}, "
              f"{len(frame)} blocks, {C.BINANCE_SYMBOL} as ground truth")
    open(os.path.join(C.OUT_DIR, "error_table.md"), "w").write("\n".join(md))

    print("\n".join(md))

    # ---- chart 1: price tracking
    fig, ax = plt.subplots(figsize=(13, 6))
    ax.plot(ts, tick_to_price(cex_tick), label="Binance (truth)", lw=2.2, color="black")
    for name in ["own_pool_ema", f"spot_{deepest}", "composite_median"]:
        if name in methods:
            ax.plot(ts, tick_to_price(methods[name]), label=name, lw=1.1, alpha=0.85)
    ax.set_xlabel("unix time"); ax.set_ylabel("USDC per ETH")
    ax.set_title("Reference price methods vs Binance")
    ax.legend(); ax.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(os.path.join(C.OUT_DIR, "tracking.png"), dpi=140)

    # ---- chart 2: error distribution
    fig, ax = plt.subplots(figsize=(11, 6))
    for name, est in methods.items():
        e = np.abs(est - cex_tick)
        e = e[~np.isnan(e)]
        if len(e):
            ax.hist(e, bins=80, histtype="step", lw=1.5, label=name, density=True)
    ax.set_xlabel("absolute error (bps)"); ax.set_ylabel("density")
    ax.set_title("Error distribution by method")
    ax.set_xlim(0, np.nanpercentile(np.abs(methods["composite_median"] - cex_tick), 99))
    ax.legend(); ax.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(os.path.join(C.OUT_DIR, "error_hist.png"), dpi=140)

    # ---- source-set ablation: does each extra pool earn its gas?
    #
    # Every source added costs a cold SLOAD or an observe() call on EVERY swap.
    # If a 4-pool median is within a fraction of a bp of a 1-pool read, the
    # extra sources are pure cost. Measure it rather than assume it.
    print("\nSource-set ablation")
    from itertools import combinations

    abl = []
    for k in range(1, len(labels) + 1):
        for subset in combinations(range(len(labels)), k):
            sub_ticks = ticks_all[:, list(subset)]
            sub_liq = liq_all[:, list(subset)]
            est = (sub_ticks[:, 0] if k == 1
                   else weighted_median(sub_ticks, sub_liq))
            e = np.abs(est - cex_tick)
            e = e[~np.isnan(e)]
            if len(e) == 0:
                continue
            share = float(np.nanmean(np.nansum(sub_liq, axis=1) /
                                     np.nansum(liq_all, axis=1)))
            abl.append({
                "sources": "+".join(labels[i] for i in subset),
                "n_sources": k,
                "mean_err_bps": round(float(e.mean()), 3),
                "p95_err_bps": round(float(np.percentile(e, 95)), 3),
                "liq_share": round(share, 3),
                "min_weight_to_corrupt": round(share / 2, 3),
            })

    ab = pd.DataFrame(abl).sort_values(["n_sources", "mean_err_bps"])
    ab.to_csv(os.path.join(C.OUT_DIR, "source_ablation.csv"), index=False)

    best1 = ab[ab.n_sources == 1].mean_err_bps.min()
    bestn = ab.mean_err_bps.min()
    print(ab.to_string(index=False))
    print(f"\n  best single source: {best1} bps")
    print(f"  best combination:   {bestn} bps")
    print(f"  accuracy gained by going multi-source: {best1 - bestn:.3f} bps")
    if best1 - bestn < 0.5:
        print("  -> Extra sources buy almost no accuracy here. Their value is")
        print("     manipulation resistance, not precision. Say so explicitly.")

    # ---- chart 3: TWAP window sweep (answers Alex's question directly)
    windows = [60, 120, 300, 600, 900, 1800, 3600, 7200]
    sweep = []
    base = frame[f"tick_{deepest}"].to_numpy()
    for w in windows:
        est = time_weighted_average(ts, base, w)
        e = np.abs(est - cex_tick)
        sweep.append({"window_s": w,
                      "mean_err_bps": float(np.nanmean(e)),
                      "p95_err_bps": float(np.nanpercentile(e, 95))})
    sw = pd.DataFrame(sweep)
    sw.to_csv(os.path.join(C.OUT_DIR, "twap_window_sweep.csv"), index=False)

    fig, ax = plt.subplots(figsize=(9, 5.5))
    ax.plot(sw.window_s, sw.mean_err_bps, "o-", label="mean error")
    ax.plot(sw.window_s, sw.p95_err_bps, "s--", label="p95 error")
    ax.set_xscale("log")
    ax.set_xlabel("TWAP window (seconds)"); ax.set_ylabel("error vs Binance (bps)")
    ax.set_title("TWAP window length vs accuracy")
    ax.legend(); ax.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(os.path.join(C.OUT_DIR, "twap_sweep.png"), dpi=140)

    print(f"\nWrote {C.OUT_DIR}/results.csv, error_table.md, "
          f"tracking.png, error_hist.png, twap_sweep.png")


if __name__ == "__main__":
    main()
