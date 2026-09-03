#!/usr/bin/env python3
"""
bucket_value.py — where does dislocation VALUE actually sit?

Appendix A2 promised this and it was never computed. Two quantities the block-share
figures cannot answer:

  * the hook is blind below gapThresholdTicks by construction — how much value is in
    that blind spot?
  * the guard freezes on spot-vs-TWAP divergence, which is exactly what a fast genuine
    move produces — is the hook switched off during the events it exists to capture?

Both need VALUE weighting, not block counting, because volatility clusters: a 10% block
share can carry far more or far less than 10% of the value.

Method. For each swap in the target pool, reconstruct what beforeSwap would see:
pre-swap target tick vs the reference (v3 0.01% spot) at that log position. Dislocation
value for that swap is |gap ticks| x swap notional in USD — the surcharge base, since
surcharge = notional x rate(gap). Bucket by |gap| and guard state.

    1  |gap| <= 65                      invisible by construction
    2  |gap| >  65, guard unfrozen      capturable
    3  |gap| >  65, guard frozen        visible but switched off

Headline is the dollar-weighted share in bucket 2.
"""
import csv, sys, math, argparse
from collections import defaultdict

USDC_DEC, WETH_DEC = 6, 18

def eth_price_from_tick(t):
    return 1e12 / (1.0001 ** t)

def load(path):
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            rows.append((int(r["block"]), int(r["logIndex"]), int(r["tick"]),
                         int(r["amount0"]), int(r["amount1"])))
    rows.sort(key=lambda x: (x[0], x[1]))
    return rows

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True)
    ap.add_argument("--fast",   required=True)
    ap.add_argument("--deep",   required=True)
    ap.add_argument("--threshold", type=int, default=65)
    ap.add_argument("--guard",     type=int, default=50)
    ap.add_argument("--twap-blocks", type=int, default=150)  # ~30 min at 12s
    a = ap.parse_args()

    tgt, fast, deep = load(a.target), load(a.fast), load(a.deep)

    # reference and guard series as (block, logIndex) -> tick, forward-filled
    def series(rows):
        return [(b, li, tk) for b, li, tk, _, _ in rows]
    fs, ds = series(fast), series(deep)

    def make_lookup(s):
        idx = 0
        def look(b, li):
            nonlocal idx
            while idx + 1 < len(s) and (s[idx+1][0], s[idx+1][1]) <= (b, li):
                idx += 1
            return s[idx][2] if s else None
        return look
    fast_at, deep_at = make_lookup(fs), make_lookup(ds)

    # rolling deep TWAP proxy: mean deep tick over the last N blocks of deep swaps
    deep_hist = []
    deep_idx = 0

    buckets = defaultdict(float)   # bucket -> value
    counts  = defaultdict(int)
    prev_tick = None

    for b, li, tk, a0, a1 in tgt:
        if prev_tick is None:
            prev_tick = tk
            continue
        ftick = fast_at(b, li)
        dtick = deep_at(b, li)
        if ftick is None or dtick is None:
            prev_tick = tk; continue

        # advance the deep history window
        while deep_idx < len(ds) and (ds[deep_idx][0], ds[deep_idx][1]) <= (b, li):
            deep_hist.append((ds[deep_idx][0], ds[deep_idx][2])); deep_idx += 1
        window = [t for blk, t in deep_hist if blk > b - a.twap_blocks]
        dtwap = sum(window)/len(window) if window else dtick

        gap = prev_tick - ftick                       # what beforeSwap would see
        px  = eth_price_from_tick(prev_tick)
        notional = max(abs(a0)/10**USDC_DEC, (abs(a1)/10**WETH_DEC)*px)
        value = abs(gap) * notional                   # surcharge base

        div = max(abs(dtick - dtwap), abs(ftick - dtick))
        if abs(gap) <= a.threshold:
            k = 1
        elif div <= a.guard:
            k = 2
        else:
            k = 3
        buckets[k] += value; counts[k] += 1
        prev_tick = tk

    total = sum(buckets.values()) or 1.0
    n = sum(counts.values()) or 1
    names = {1: f"|gap| <= {a.threshold}  (invisible by construction)",
             2: f"|gap| >  {a.threshold}, guard unfrozen  (CAPTURABLE)",
             3: f"|gap| >  {a.threshold}, guard frozen     (visible, switched off)"}
    print(f"\ntarget={a.target}")
    print(f"swaps analysed: {n:,}\n")
    print(f"{'bucket':<52}{'swaps':>10}{'swap %':>9}{'value %':>10}")
    print("-"*81)
    for k in (1, 2, 3):
        print(f"{names[k]:<52}{counts[k]:>10,}{100*counts[k]/n:>8.2f}%{100*buckets[k]/total:>9.2f}%")
    print("-"*81)
    print(f"\nBOOLEAN-GUARD DESIGN (freeze on divergence)")
    print(f"  capturable:                        {100*buckets[2]/total:6.2f}%")
    print(f"  lost to the threshold blind spot:  {100*buckets[1]/total:6.2f}%")
    print(f"  lost to guard freezing:            {100*buckets[3]/total:6.2f}%")
    print(f"\nSHIPPED DESIGN (divergence priced, never withheld)")
    print(f"  capturable at 1.00x:               {100*buckets[2]/total:6.2f}%")
    print(f"  capturable at a raised multiplier: {100*buckets[3]/total:6.2f}%")
    print(f"  CAPTURABLE IN TOTAL:               {100*(buckets[2]+buckets[3])/total:6.2f}%")
    print(f"  lost to the threshold blind spot:  {100*buckets[1]/total:6.2f}%")

main()
