"""
Does the capture mechanism survive a truncated reference?

    python truncation_capture.py             # measure against data/
    python truncation_capture.py --selftest  # verify the math, no data needed

Reads data/*.csv (same files fetch.py produces), writes:
    out/truncation_capture.csv    per-(B, target) metrics, event-level
    out/truncation_table.md       paste straight into the appendix
    out/truncation_capture.png    capture fraction vs B
    out/truncation_moves.png      honest per-block reference moves, p99 marked

The question being answered
---------------------------
Appendix §10 measured that the divergence guard is defeatable for $7-$21 and
concluded the fix is a per-block movement bound on the reference (the Truncated
Oracle pattern): cap how far the reported tick may move per block, so a
one-shot push becomes a sustained, contested, per-block cost.

But a bounded reference is by construction a slow reference. When the market
gaps N ticks in one block, the truncated tick crawls at B ticks per block while
arbitrageurs correct the pool almost immediately. If the pool is corrected
before the truncated gap ever crosses gapThresholdTicks, no gap opens and
nothing is captured — and the events lost this way are exactly the fast
exogenous moves the empty-ledger rule was built for.

So the direction of the whole project reduces to one number:

    What fraction of dislocation value arrives SLOWLY enough
    for a truncated reference to see it?

High  -> truncation fixes §10 at acceptable cost: capture is the product.
Low   -> capture is mostly dead even with a manipulation-proof reference:
         the asymmetric-fee flip is the product and capture is a side effect.

Why event-level, not block-level
--------------------------------
A first version of this measurement used per-block forward-filled tick series
and found ZERO dislocations on the two active target pools. That was a
granularity artifact: when the market moves and the arb corrects within a
block or two, both END-OF-BLOCK ticks look fine — but the HOOK is not an
end-of-block observer. beforeSwap fires mid-block, before the correcting swap,
and sees the full gap. The swap CSVs carry logIndex, so the honest measurement
is per swap event:

    for each target-pool swap:  gap_pre = (pool tick before this swap)
                                        - (reference tick at this exact
                                           point in the block ordering)

which is literally the quantity beforeSwap computes. Measured this way, the
active pools DO show dislocations — rare, fast, and large (hundreds of ticks)
— that block-level series cannot see. The block-level engine is kept only for
the honest movement distribution that suggests B, and clearly labelled.

What is simulated
-----------------
Reference = truncated spot tick of the fast pool (v3 0.01%), truncated-oracle
semantics: the reported tick may move at most B ticks per elapsed block, with
catch-up over idle blocks —

    on a read at block b, raw tick r, last update (value v, block b0):
        allowed = B * (b - b0)          # b > b0; intra-block reads share
        report    v + clamp(r - v, ±allowed)   # the block's budget

The divergence guard is NOT simulated. §10 shows the guard is an off-switch
(one push past guardMaxDevTicks disables all capture for ~$21), so the
truncated design REPLACES it: the reference always reports, and manipulation
resistance comes from the bound alone. The only remaining freeze is a hard
read failure, which an attacker cannot induce and which we do not model.

Governed pool = a real v3 pool's swap history, three flow regimes:
    v3_005  deep, busy      — mature-pool stand-in
    v3_030  thin            — mid-flow stand-in
    v3_100  nearly dead     — day-one-pool stand-in

The same stand-in logic config.py uses for the own-EMA baseline, with the same
caveat: none of these is a Backdraft pool. What the stand-ins provide is real
correction latency — the historical record already contains how fast
arbitrageurs actually closed each dislocation, and that is precisely the clock
the truncated reference is racing.

Episode classification — who closed the gap?
--------------------------------------------
Not every hook-visible gap is a stale pool. Inspecting this window showed most
large gaps on the busy target are transient spikes IN THE REFERENCE ITSELF
(the thin 0.01% pool pushed hundreds of ticks and arbed back within blocks,
while the target never moved). A raw spot reference opens a gap on a correctly
priced pool and mischarges whoever swaps during the spike; §10's guard existed
for exactly this, and §10 showed the guard is defeatable. So each episode is
classified by its resolution:

    REAL     the target pool moved to the reference — a genuine dislocation;
             capturing it is the mechanism working
    GLITCH   the reference reverted to the target — the reference was wrong;
             "capturing" it is a mischarge

Mechanically: at the episode's peak-gap event and at the first event after the
episode, split the gap closure into the target's contribution and the
reference's contribution; REAL iff the target contributed the majority.
Episodes still open at the end of the data are dropped as unresolved.

Value captured is then reported OVER REAL EPISODES ONLY — that is THE number —
and glitch episodes are scored the opposite way: the fraction of their phantom
value the truncated reference refused to see is mischarge avoided, a benefit.

Episodes and metrics (event-level)
----------------------------------
An episode is a maximal run of consecutive target-pool swaps each entered with
|gap_raw_pre| > GAP_THRESHOLD_TICKS (runs separated by a single below-threshold
event are merged — one noise trade mid-dislocation is not two dislocations).
For each episode, at each B:

    opened      did |gap_trunc_pre| exceed threshold at any swap in the
                episode? (no open -> the raw hook charges, the truncated
                hook charges nothing)
    peak_raw    max |gap_raw_pre| in the episode — value proxy: the hook
                prices surcharges on maxAbsGap, so per unit of closing
                notional, captured value is proportional to the peak
    peak_trunc  max |gap_trunc_pre| in the episode if opened, else 0
    delay       swaps into the episode before the truncated gap first
                crosses threshold — the arbitrage that stays free even
                when the gap eventually opens

Aggregates per (B, target):

    episodes_opened_pct   share of episodes where a gap opened at all
    value_captured_pct    sum(peak_trunc) / sum(peak_raw)   <- THE number
    median_open_delay     swaps, over opened episodes only

Phantom gaps — the cost truncation adds
---------------------------------------
After a fast move the pool is corrected within blocks, but the truncated
reference is still crawling: |gap_trunc| is large ON A CORRECTLY PRICED pool.
A gap opens against the lagging reference, and any swap classified "narrowing"
(toward the stale reference = away from the true price) pays a peak-rate
surcharge it does not deserve. Counted on real swaps:

    phantom_events        target swaps with |gap_trunc_pre| > threshold
                          but |gap_raw_pre| <= threshold
    phantom_tick_events   sum of (|gap_trunc_pre| - threshold) over them
                          — mischarge exposure, in ticks

A B that captures well but phantoms constantly has not shrunk the blind spot,
it has moved the tax onto honest flow. Both columns go in the table.

Choosing B
----------
Same style as the 65-tick threshold (derived from measured p100 error, §7):
B is set at the p99 of HONEST per-block reference moves. Below that,
truncation almost never binds in calm operation; a manipulator must exceed
the p99 honest move every block, sustained, which is the signature we want
attacks to have. The sweep runs fixed B values plus this suggested one.

Caveats to state when publishing
--------------------------------
- Stand-in pools, not a Backdraft pool. v3_100's near-dead flow makes gaps
  large and slow (optimistic for capture); v3_005 hugs the reference
  (pessimistic). Publish all rows; do not average them.
- The stand-ins' correction speed embeds vanilla economics. A live surcharge
  raises the arb break-even and slows correction, which HELPS the crawling
  reference; measured capture is a mild lower bound in that one respect.
- Peak-gap weighting prices surcharge per unit notional; per-episode closing
  notional is in the CSVs and a follow-up should weight by it.
- Same-block ordering between the reference pool and the target pool is taken
  from logIndex, which is correct for these two v3 pools but would not
  capture a searcher bundling both legs around the hook's own callbacks.
- One pair, one window. Same as every other number in the appendix.
"""

import argparse
import glob
import math
import os
import re
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import config as C

# ---------------------------------------------------------------- parameters

GAP_THRESHOLD_TICKS = 65          # hook cfg.gapThresholdTicks — appendix §7
MERGE_EVENTS        = 1           # one sub-threshold swap does not split an episode
B_VALUES            = [5, 10, 20, 30, 50, 100]   # suggested B appended at runtime

REF_LABEL           = "v3_001"    # the fast pool: the hook's reference source
TARGET_LABELS       = ["v3_005", "v3_030", "v3_100"]

# Verdict thresholds on value_captured_pct, fixed before running so the number
# decides and not the narrative. Applied per flow regime; the print-out at the
# end interprets the spread rather than averaging it away.
VERDICT_CAPTURE     = 0.50
VERDICT_FLIP        = 0.25


# ---------------------------------------------------------------- data loading

def find_tag():
    """
    Prefer START_BLOCK/END_BLOCK from the environment (same override scheme as
    fetch.py); otherwise auto-detect from whatever reference-pool file exists,
    so the script runs against cached data without editing config.py.
    """
    env_tag = f"{C.START_BLOCK}_{C.END_BLOCK}"
    if os.path.exists(os.path.join(C.DATA_DIR, f"swaps_{REF_LABEL}_{env_tag}.csv")):
        return env_tag
    hits = sorted(glob.glob(os.path.join(C.DATA_DIR, f"swaps_{REF_LABEL}_*.csv")))
    if not hits:
        sys.exit(f"no data/swaps_{REF_LABEL}_*.csv — run fetch.py first")
    m = re.search(rf"swaps_{REF_LABEL}_(\d+_\d+)\.csv$", hits[-1])
    return m.group(1)


def load_events(label, tag):
    """One row per swap: block, logIndex, tick — in exact on-chain order."""
    df = pd.read_csv(os.path.join(C.DATA_DIR, f"swaps_{label}_{tag}.csv"))
    if df.empty:
        return None
    df = df[["block", "logIndex", "tick"]].copy()
    df["tick"] = pd.to_numeric(df["tick"], errors="coerce").astype(float)
    return df.sort_values(["block", "logIndex"]).reset_index(drop=True)


# ---------------------------------------------------------------- truncated oracle

class TruncatedRef:
    """
    Truncated-oracle semantics over an event stream.

    State is (value, block_of_last_advance, budget_base). Within one block all
    reads share a movement budget of B * blocks_elapsed measured from the value
    the reference held when the block began — a second read in the same block
    cannot double-spend the budget.
    """

    def __init__(self, bound, first_tick, first_block):
        self.bound = bound
        self.value = float(first_tick)
        self.block = int(first_block)
        self.base = float(first_tick)      # value at the start of self.block
        self.allowed = 0.0                 # budget for self.block

    def read(self, raw_tick, block):
        if block > self.block:
            self.base = self.value
            self.allowed = self.bound * (block - self.block)
            self.block = block
        step = float(raw_tick) - self.base
        if step > self.allowed:
            step = self.allowed
        elif step < -self.allowed:
            step = -self.allowed
        self.value = self.base + step
        return self.value


# ---------------------------------------------------------------- measurement

def hook_view(ref_ev, tgt_ev, bound):
    """
    Replay both event streams in on-chain order and record, for every target
    swap after the first, exactly what beforeSwap would see:

        gap_raw_pre    pre-swap pool tick - raw reference tick
        gap_trunc_pre  pre-swap pool tick - truncated reference tick

    The pre-swap pool tick is the previous target swap's (post-swap) tick —
    the pool's price has not moved since, by definition.
    """
    ref = ref_ev[["block", "logIndex", "tick"]].copy();  ref["src"] = 0
    tgt = tgt_ev[["block", "logIndex", "tick"]].copy();  tgt["src"] = 1
    ev = pd.concat([ref, tgt]).sort_values(
        ["block", "logIndex"]).reset_index(drop=True)

    # Start the truncated reference at the first raw reference value seen.
    first_ref = ref.iloc[0]
    tr = TruncatedRef(bound, first_ref["tick"], first_ref["block"])

    raw_ref = float(first_ref["tick"])
    pre_tick = None
    rows = []
    for r in ev.itertuples(index=False):
        if r.src == 0:
            raw_ref = float(r.tick)
            continue
        trunc_ref = tr.read(raw_ref, int(r.block))
        if pre_tick is not None:
            rows.append({
                "block": int(r.block),
                "pre_tick": pre_tick,
                "ref_raw": raw_ref,
                "ref_trunc": trunc_ref,
                "gap_raw_pre": pre_tick - raw_ref,
                "gap_trunc_pre": pre_tick - trunc_ref,
            })
        pre_tick = float(r.tick)
    return pd.DataFrame(rows)


def episodes_from_events(above):
    """
    Maximal runs of True in `above`, merging runs separated by <= MERGE_EVENTS
    False entries. Returns list of (start_idx, end_idx) inclusive.
    """
    idx = np.flatnonzero(above)
    if len(idx) == 0:
        return []
    eps, start, prev = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - prev > MERGE_EVENTS + 1:
            eps.append((start, prev))
            start = i
        prev = i
    eps.append((start, prev))
    return eps


def classify_episode(view, s, e, pk):
    """
    REAL iff the target's move contributed the majority of the gap closure
    between the peak event and the first event after the episode. Returns
    "real", "glitch", or "unresolved" (episode still open at end of data).
    """
    if e + 1 < len(view):
        r = e + 1                       # first event after the episode
    elif e > pk:
        r = e                           # episode runs to end of data: classify
                                        # on within-episode convergence instead
    else:
        return "unresolved"             # peak IS the last event — nothing to
                                        # measure convergence against
    g_pk = view["gap_raw_pre"].iloc[pk]
    sgn = 1.0 if g_pk > 0 else -1.0
    d_tgt = view["pre_tick"].iloc[r] - view["pre_tick"].iloc[pk]
    d_ref = view["ref_raw"].iloc[r] - view["ref_raw"].iloc[pk]
    ct = max(0.0, -d_tgt * sgn)      # target moving toward the reference
    cr = max(0.0, d_ref * sgn)       # reference moving toward the target
    if ct + cr == 0:
        return "unresolved"
    return "real" if ct / (ct + cr) >= 0.5 else "glitch"


def measure(view, threshold):
    """All per-episode and phantom metrics for one (target, B) hook view."""
    abs_raw = view["gap_raw_pre"].abs().to_numpy()
    abs_trunc = view["gap_trunc_pre"].abs().to_numpy()

    rows = []
    for (s, e) in episodes_from_events(abs_raw > threshold):
        seg_raw, seg_trunc = abs_raw[s:e + 1], abs_trunc[s:e + 1]
        pk = s + int(seg_raw.argmax())
        crossed = np.flatnonzero(seg_trunc > threshold)
        opened = len(crossed) > 0
        rows.append({
            "start": s, "end": e, "events": e - s + 1,
            "kind": classify_episode(view, s, e, pk),
            "peak_raw": float(seg_raw.max()),
            "peak_trunc": float(seg_trunc.max()) if opened else 0.0,
            "opened": opened,
            "delay": int(crossed[0]) if opened else np.nan,
        })
    ep = pd.DataFrame(rows)

    phantom = (abs_trunc > threshold) & (abs_raw <= threshold)
    agg = {
        "phantom_events": int(phantom.sum()),
        "phantom_tick_events": float((abs_trunc[phantom] - threshold).sum()),
    }
    if ep.empty:
        agg.update(episodes=0, real_eps=0, glitch_eps=0, unresolved_eps=0,
                   real_captured_pct=np.nan, glitch_rejected_pct=np.nan,
                   median_open_delay=np.nan)
        return ep, agg

    real = ep[ep["kind"] == "real"]
    glitch = ep[ep["kind"] == "glitch"]
    agg.update(
        episodes=len(ep),
        real_eps=len(real),
        glitch_eps=len(glitch),
        unresolved_eps=int((ep["kind"] == "unresolved").sum()),
        real_captured_pct=(float(real["peak_trunc"].sum() / real["peak_raw"].sum())
                           if len(real) and real["peak_raw"].sum() > 0 else np.nan),
        glitch_rejected_pct=(1.0 - float(glitch["peak_trunc"].sum()
                                         / glitch["peak_raw"].sum())
                             if len(glitch) and glitch["peak_raw"].sum() > 0
                             else np.nan),
        median_open_delay=(float(ep.loc[ep["opened"], "delay"].median())
                           if ep["opened"].any() else np.nan),
    )
    return ep, agg


# ---------------------------------------------------------------- block-level B

def honest_move_distribution(ref_ev, tag):
    """
    Per-block moves of the raw reference under honest conditions — the input
    to the §7-style choice of B. Block-level is CORRECT here (the bound is per
    block); it was only wrong as a dislocation detector.
    """
    bt = pd.read_csv(os.path.join(C.DATA_DIR, f"block_times_{tag}.csv"))
    last = ref_ev.groupby("block").last().reset_index()[["block", "tick"]]
    s = bt.sort_values("block").merge(last, on="block", how="left")
    ticks = s["tick"].ffill().bfill().to_numpy()
    return np.abs(np.diff(ticks))


# ---------------------------------------------------------------- outputs

def fmt_pct(x):
    return "—" if pd.isna(x) else f"{100 * x:.1f}%"


def write_table(results, suggested_b, out_path):
    lines = [
        "| target | B (ticks/block) | eps (real/glitch) | real value captured | glitch value rejected | phantom swaps | phantom ticks |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        b = f"**{r['B']}**" if r["B"] == suggested_b else (
            "∞ (raw)" if np.isinf(r["B"]) else str(r["B"]))
        lines.append(
            f"| {r['target']} | {b} | {r['episodes']} ({r['real_eps']}/{r['glitch_eps']}) "
            f"| {fmt_pct(r['real_captured_pct'])} | {fmt_pct(r['glitch_rejected_pct'])} "
            f"| {r['phantom_events']} | {r['phantom_tick_events']:.0f} |")
    lines += [
        "",
        f"*Event-level: gaps are measured per target-pool swap as beforeSwap "
        f"would see them (pre-swap pool tick vs the reference at that exact "
        f"log position). B = **{suggested_b}** is the p99 of honest per-block "
        f"reference moves, derived the same way §7 derived the 65-tick "
        f"threshold. Episodes are classified by who closed the gap: 'real' = "
        f"the target pool converged to the reference (genuine staleness), "
        f"'glitch' = the reference reverted to the target (the reference was "
        f"wrong). 'Real value captured' is peak-gap-weighted capture over real "
        f"episodes — the mechanism working. 'Glitch value rejected' is the "
        f"share of phantom peak value the truncated reference refused to see — "
        f"mischarge avoided; for the raw reference this is 0% by definition. "
        f"'Phantom' counts swaps entered with a truncated gap open while no "
        f"raw dislocation existed.*",
    ]
    with open(out_path, "w") as f:
        f.write("\n".join(lines) + "\n")


def plot_capture(results, suggested_b, out_path):
    fig, ax = plt.subplots(figsize=(8, 5))
    for tgt in TARGET_LABELS:
        rs = sorted((r for r in results
                     if r["target"] == tgt and not np.isinf(r["B"])),
                    key=lambda r: r["B"])
        if not rs or all(pd.isna(r["real_captured_pct"]) for r in rs):
            continue
        ax.plot([r["B"] for r in rs],
                [100 * (r["real_captured_pct"] if not pd.isna(r["real_captured_pct"]) else 0)
                 for r in rs], marker="o", label=tgt)
    ax.axvline(suggested_b, ls="--", color="gray", lw=1)
    ax.text(suggested_b, ax.get_ylim()[1] * 0.95,
            f"  p99 honest move = {suggested_b}", fontsize=8, color="gray")
    ax.axhline(100 * VERDICT_CAPTURE, ls=":", color="green", lw=1)
    ax.axhline(100 * VERDICT_FLIP, ls=":", color="red", lw=1)
    ax.set_xlabel("truncation bound B (ticks per block)")
    ax.set_ylabel("REAL dislocation value captured (%)")
    ax.set_title("Capture surviving a truncated reference, by governed-pool flow regime")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def plot_moves(deltas, suggested_b, out_path):
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.hist(deltas, bins=100, log=True)
    ax.axvline(suggested_b, ls="--", color="red", lw=1,
               label=f"p99 = {suggested_b} ticks")
    ax.set_xlabel("|Δ reference tick| per block (honest)")
    ax.set_ylabel("blocks (log)")
    ax.set_title(f"Honest per-block moves of the {REF_LABEL} reference")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------- self-test

def _mk(events):
    """events: list of (block, logIndex, tick) -> DataFrame in stream shape."""
    return pd.DataFrame(events, columns=["block", "logIndex", "tick"]).astype(
        {"block": int, "logIndex": int, "tick": float})


def selftest():
    # 1. Truncated-oracle semantics: per-block budget, intra-block no
    #    double-spend, catch-up across idle blocks.
    tr = TruncatedRef(bound=20, first_tick=0, first_block=1)
    assert tr.read(300, 2) == 20, "one block elapsed -> at most B"
    assert tr.read(300, 2) == 20, "second read in same block: budget shared"
    assert tr.read(300, 5) == 80, "three idle blocks -> 3B catch-up"
    assert tr.read(60, 6) == 60, "within budget -> track raw exactly"

    # 2. Fast dislocation on a BUSY pool: swaps every block keep the oracle's
    #    per-block budget drained, so when the shock lands the crawl has only
    #    B of headroom. The raw hook sees a 300-tick pre-swap gap; the
    #    truncated hook must NOT open, and the correction must leave phantom
    #    exposure on the following swaps. (With idle blocks before the shock
    #    the truncated oracle accumulates budget and legitimately catches it —
    #    catch-up semantics — which is why the busy case is the hard one.)
    ref = _mk([(1, 0, 0), (10, 0, 300)])            # CEX shock at block 10
    tgt = _mk([(b, 5, 0) for b in range(2, 10)] +   # steady flow at old price
              [(10, 9, 300),                        # arb corrects, pre-gap=300
               (11, 3, 300), (12, 3, 300), (13, 3, 300)])   # honest flow after
    view = hook_view(ref, tgt, bound=20)
    raw_view = hook_view(ref, tgt, bound=np.inf)
    assert (raw_view["gap_raw_pre"].abs() > 65).sum() == 1, "raw sees the arb"
    _, agg = measure(view, 65)
    assert agg["episodes"] == 1
    assert agg["real_eps"] == 1, "target closed the gap -> classified real"
    assert pd.isna(agg["real_captured_pct"]) or agg["real_captured_pct"] == 0.0, \
        "fast real move must be missed at B=20"
    assert agg["phantom_events"] > 0, "crawl after correction must phantom"

    # 2b. Reference glitch: the reference spikes and reverts while the target
    #     never moves. Must be classified glitch; truncation must reject its
    #     value entirely (glitch_rejected_pct == 1).
    ref = _mk([(b, 0, 0) for b in range(1, 10)] +
              [(10, 0, 300), (12, 0, 0)])           # spike at 10, revert at 12
    tgt = _mk([(b, 5, 0) for b in range(2, 16)])    # busy pool, flat price
    view = hook_view(ref, tgt, bound=20)
    _, agg = measure(view, 65)
    assert agg["episodes"] >= 1
    assert agg["glitch_eps"] == agg["episodes"], "reverting ref -> glitch"
    assert agg["real_eps"] == 0
    assert agg["glitch_rejected_pct"] == 1.0, "truncation must reject the spike"
    raw_view = hook_view(ref, tgt, bound=np.inf)
    _, agg_raw = measure(raw_view, 65)
    assert agg_raw["glitch_rejected_pct"] == 0.0, "raw mischarges the spike fully"

    # 3. Slow drift against a stale pool: reference ramps 10 ticks/block while
    #    the pool sleeps; the truncated ref (B=20) keeps pace with raw, so the
    #    gap opens and capture is near-total.
    ref = _mk([(b, 0, (b - 1) * 10.0) for b in range(1, 31)])
    tgt = _mk([(1, 5, 0.0), (15, 5, 0.0), (28, 5, 0.0), (29, 5, 280.0),
               (30, 5, 280.0)])   # post-convergence event so the episode resolves
    view = hook_view(ref, tgt, bound=20)
    _, agg = measure(view, 65)
    assert agg["episodes"] == 1
    assert agg["real_eps"] == 1, "stale pool converging -> real"
    assert agg["real_captured_pct"] > 0.9, "slow drift must be captured"

    # 4. B=inf reproduces the raw hook exactly.
    view_inf = hook_view(ref, tgt, bound=np.inf)
    assert np.allclose(view_inf["gap_raw_pre"], view_inf["gap_trunc_pre"])
    _, agg = measure(view_inf, 65)
    assert abs(agg["real_captured_pct"] - 1.0) < 1e-12

    # 5. Episode merging: one sub-threshold swap inside a dislocation does not
    #    split it into two episodes.
    view = pd.DataFrame({
        "gap_raw_pre":  [100.0, 100, 10, 100, 100, 0, 0],
        "gap_trunc_pre": [0.0] * 7,
        "pre_tick": [100.0, 100, 10, 100, 100, 0, 0],
        "ref_raw": [0.0] * 7, "ref_trunc": [0.0] * 7, "block": [1] * 7,
    })
    ep, _ = measure(view, 65)
    assert len(ep) == 1, "jitter must not split an episode"

    print("selftest: all checks pass")


# ---------------------------------------------------------------- main

def main():
    tag = find_tag()
    ref_ev = load_events(REF_LABEL, tag)
    if ref_ev is None:
        sys.exit(f"reference pool {REF_LABEL} has no swaps in window {tag}")

    deltas = honest_move_distribution(ref_ev, tag)
    p = {q: float(np.percentile(deltas, q)) for q in (50, 90, 95, 99, 99.9)}
    suggested_b = max(1, math.ceil(p[99]))
    bounds = sorted(set(B_VALUES + [suggested_b])) + [np.inf]

    print(f"window {tag}: {len(ref_ev)} reference swaps")
    print(f"honest |Δref|/block: p50={p[50]:.1f} p90={p[90]:.1f} "
          f"p95={p[95]:.1f} p99={p[99]:.1f} p99.9={p[99.9]:.1f} "
          f"max={deltas.max():.0f}")
    print(f"suggested B = ceil(p99) = {suggested_b} ticks/block\n")

    results = []
    for tgt_label in TARGET_LABELS:
        tgt_ev = load_events(tgt_label, tag)
        if tgt_ev is None or len(tgt_ev) < 2:
            print(f"  {tgt_label}: not enough swaps in window, skipped")
            continue
        for b in bounds:
            view = hook_view(ref_ev, tgt_ev, b)
            _, agg = measure(view, GAP_THRESHOLD_TICKS)
            results.append({"target": tgt_label, "B": b, **agg})

    os.makedirs(C.OUT_DIR, exist_ok=True)
    pd.DataFrame(results).to_csv(
        os.path.join(C.OUT_DIR, "truncation_capture.csv"), index=False)
    write_table(results, suggested_b,
                os.path.join(C.OUT_DIR, "truncation_table.md"))
    plot_capture(results, suggested_b,
                 os.path.join(C.OUT_DIR, "truncation_capture.png"))
    plot_moves(deltas, suggested_b,
               os.path.join(C.OUT_DIR, "truncation_moves.png"))

    print(f"{'target':>8} {'B':>7} {'eps':>5} {'real':>5} {'glitch':>7} "
          f"{'real_cap':>9} {'glit_rej':>9} {'phantom':>8}")
    for r in results:
        b = "raw" if np.isinf(r["B"]) else str(int(r["B"]))
        print(f"{r['target']:>8} {b:>7} {r['episodes']:>5} {r['real_eps']:>5} "
              f"{r['glitch_eps']:>7} {fmt_pct(r['real_captured_pct']):>9} "
              f"{fmt_pct(r['glitch_rejected_pct']):>9} {r['phantom_events']:>8}")

    # ---------------- verdict ------------------------------------------------
    print()
    at_b = {r["target"]: r for r in results if r["B"] == suggested_b}
    with_eps = {t: r for t, r in at_b.items() if r["episodes"] > 0}
    if not with_eps:
        print("verdict: no dislocations anywhere in this window — widen the "
              "window before deciding anything.")
        return
    for t, r in at_b.items():
        if r["episodes"] == 0:
            print(f"{t}: zero hook-visible dislocations in this window — "
                  f"at threshold {GAP_THRESHOLD_TICKS} the mechanism never fires "
                  f"on this flow regime at all. That is itself a finding about "
                  f"where Backdraft's revenue lives.")
            continue
        v, g = r["real_captured_pct"], r["glitch_rejected_pct"]
        if pd.isna(v):
            tag_ = "no real episodes — every hook-visible gap was a reference glitch"
        elif v >= VERDICT_CAPTURE:
            tag_ = "REAL capture survives truncation"
        elif v >= VERDICT_FLIP:
            tag_ = "real capture partially survives — publish the split"
        else:
            tag_ = "the crawl misses real value — fee flip is the product here"
        print(f"{t}: {r['episodes']} eps ({r['real_eps']} real / "
              f"{r['glitch_eps']} glitch), real capture {fmt_pct(v)}, "
              f"glitch rejection {fmt_pct(g)}, "
              f"{r['phantom_events']} phantom swaps -> {tag_}")
    print("\ncaveat: one pair, one window, stand-in pools. Windows measured so far "
          "(ETH/USDC): 25821306-25828484 volatile, 25527155-25534325 calm, "
          "25598870-25606053 volatile. Across those three the glitch-rejection "
          "result replicated (100% / 97.2% / 94.6% at the suggested B, decaying "
          "monotonically to 0% for the raw reference), but only 2 REAL episodes "
          "appeared in total — so real-capture percentages here are under-powered "
          "and must not be quoted without their n. A second token pair is the "
          "outstanding gap before the appendix ships.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
    else:
        main()