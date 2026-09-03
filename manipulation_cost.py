#!/usr/bin/env python3
"""
manipulation_cost.py — what does it cost to hide a dislocation from Backdraft?

THE ATTACK
----------
Backdraft prices the reference from the SPOT tick of the v3 0.01% pool, and freezes only
when |fast - deep| exceeds guardMaxDevTicks (50). So there is a free budget: an attacker
may push the fast pool up to 49 ticks without tripping the guard.

If the true dislocation is G ticks, pushing the reference (G - gapThreshold + 1) ticks
TOWARD the v4 pool price makes the apparent gap fall below gapThresholdTicks. No gap
opens, the arbitrageur closes the whole dislocation surcharge-free, then unwinds the
push. The pool chosen for freshness is also the thinnest, so the push is cheap exactly
where the prize is large.

WHAT THIS MEASURES
------------------
  cost   = round-trip swap fees to move the 0.01% pool N ticks and unwind, plus gas
  prize  = the surcharge avoided on the arbitrageur's own closing trade
  verdict= the arb notional at which prize exceeds cost (break-even)

Below break-even the attack loses money and the guard is adequate. Above it, the attack
pays, and the honest thing is to publish the number as a limitation rather than let a
judge derive it live.

WHAT THIS DOES *NOT* MODEL (state these alongside any figure)
------------------------------------------------------------
  * Constant liquidity across the pushed range. Real v3 crosses initialised ticks and L
    changes. Near spot on a major pair L is usually deepest, so crossing outward tends to
    make the push CHEAPER than modelled -> the cost here is an UPPER bound, i.e. this is
    generous to the defence. Say so.
  * Other searchers competing during the push. Atomic bundles make this mostly moot.
  * The attacker also pays price impact if they cannot unwind atomically. Assumed atomic.
  * Reference-pool fee revenue the attacker pays goes to that pool's LPs, not to us.

USAGE
-----
  # against your refbench data
  python manipulation_cost.py --csv refbench/data/swaps_v3_001_volatile.csv

  # sanity-check the arithmetic with no data at all
  python manipulation_cost.py --selftest

  # explore without data: sweep plausible in-range liquidity
  python manipulation_cost.py --sweep
"""

import argparse
import csv
import glob
import math
import os
import sys

# --- Backdraft parameters, from idea.md §4.5 -------------------------------------------
GAP_THRESHOLD_TICKS = 65      # gaps below this are invisible by construction
GUARD_MAX_DEV_TICKS = 50      # the attacker's free budget
CAPTURE_RATE_BPS    = 500     # bps of surcharge per tick of gap
SURCHARGE_CAP_BPS   = 200     # ceiling on the surcharge rate

# --- Pool facts ------------------------------------------------------------------------
FAST_FEE_BPS   = 1            # 0.01% tier — the reference pool being pushed
USDC_DECIMALS  = 6
WETH_DECIMALS  = 18
# ETH/USDC on mainnet: token0 = USDC, token1 = WETH (USDC address sorts lower)

GAS_PER_PUSH_SWAP = 150_000   # push + unwind, generously


def sqrt_price_at_tick(tick: float) -> float:
    """Raw sqrt(token1/token0) at a tick. Uniswap's tick base is 1.0001."""
    return 1.0001 ** (tick / 2.0)


def eth_price_from_tick(tick: float) -> float:
    """USDC per ETH, correcting for the 6/18 decimal split."""
    raw_p = sqrt_price_at_tick(tick) ** 2          # WETH_raw per USDC_raw
    return 1e12 / raw_p


def push_notional_usd(liquidity: float, tick: float, ticks_to_push: int) -> float:
    """
    USD notional an attacker must swap to move the pool `ticks_to_push` ticks.

    Constant-L within the range (see caveats). Direction is symmetric to first order,
    so this reports the cheaper of the two legs, which is the attacker's choice.

        moving up   (token1 in): d1 = L * (sqrtP1 - sqrtP0)      [WETH raw]
        moving down (token0 in): d0 = L * (1/sqrtP1 - 1/sqrtP0)  [USDC raw]
    """
    s0 = sqrt_price_at_tick(tick)
    up = sqrt_price_at_tick(tick + ticks_to_push)
    dn = sqrt_price_at_tick(tick - ticks_to_push)
    px = eth_price_from_tick(tick)

    weth_raw = liquidity * abs(up - s0)
    usd_up   = (weth_raw / 10 ** WETH_DECIMALS) * px

    usdc_raw = liquidity * abs(1.0 / dn - 1.0 / s0)
    usd_dn   = usdc_raw / 10 ** USDC_DECIMALS

    return min(usd_up, usd_dn)


def attack_cost_usd(liquidity, tick, ticks_to_push, gas_price_gwei, eth_price):
    """Round-trip fees on the push and unwind, plus gas."""
    notional = push_notional_usd(liquidity, tick, ticks_to_push)
    fee_usd  = 2.0 * notional * (FAST_FEE_BPS / 10_000.0)   # push, then unwind
    gas_usd  = (2 * GAS_PER_PUSH_SWAP) * gas_price_gwei * 1e-9 * eth_price
    return {
        "push_notional_usd": notional,
        "fee_usd": fee_usd,
        "gas_usd": gas_usd,
        "total_usd": fee_usd + gas_usd,
    }


def surcharge_avoided_usd(arb_notional_usd: float, gap_ticks: int) -> float:
    """
    What the arbitrageur would have paid Backdraft on their closing trade.
    Mirrors SurchargeMath.compute after the task-9 precision fix:
        rate = min(gap * captureRateBps / 1e8, capBps / 1e4)
    """
    uncapped = gap_ticks * CAPTURE_RATE_BPS / 1e8
    capped   = SURCHARGE_CAP_BPS / 1e4
    return arb_notional_usd * min(uncapped, capped)


def ticks_needed_to_mask(gap_ticks: int) -> int:
    """Push required to bring an apparent gap under the detection threshold."""
    return max(0, gap_ticks - GAP_THRESHOLD_TICKS + 1)


def rate_at_gap(gap_ticks: int) -> float:
    """Surcharge rate as a fraction, matching SurchargeMath after task 9."""
    return min(gap_ticks * CAPTURE_RATE_BPS / 1e8, SURCHARGE_CAP_BPS / 1e4)


def prize_rate(gap_ticks: int, ticks_pushed: int) -> float:
    """
    Fraction of notional the attacker avoids paying.

    Full mask  -> the gap never opens, so the WHOLE surcharge is avoided.
    Partial    -> the gap still opens, but on a smaller maxAbsGap, so only the
                  difference is avoided. This is the case the first version of this
                  script got wrong: it credited the attacker with the full surcharge
                  even when the mask was too small to hide the gap, which overstated
                  the attack's profitability on every gap above 113 ticks.
    """
    if ticks_pushed >= ticks_needed_to_mask(gap_ticks):
        return rate_at_gap(gap_ticks)
    apparent = gap_ticks - ticks_pushed
    return max(0.0, rate_at_gap(gap_ticks) - rate_at_gap(apparent))


# The hook no longer freezes on divergence — it PRICES it (DivergenceMath). That removes
# the cliff this model was built around. Set GRADUATED=False to reproduce the old
# boolean-guard numbers.
GRADUATED = True


def analyse(liquidity, tick, gap_ticks, gas_price_gwei, graduated=None):
    eth_price = eth_price_from_tick(tick)
    need = ticks_needed_to_mask(gap_ticks)

    if GRADUATED if graduated is None else graduated:
        # No cliff. Pushing past guardMaxDevTicks raises divTicks, which raises the
        # surcharge MULTIPLIER — but a full mask means no gap opens, so there is no
        # surcharge for the multiplier to multiply. The deterrent does not bind on the
        # one attack it most needs to deter, and the push is limited only by its cost.
        # (freezeMaxDevTicks does not help either: crossing it withholds the reference,
        # which also prevents the gap from opening. Both outcomes are a successful mask.)
        feasible = True
        push = need
    else:
        feasible = need <= GUARD_MAX_DEV_TICKS - 1
        push = min(need, GUARD_MAX_DEV_TICKS - 1)

    cost = attack_cost_usd(liquidity, tick, push, gas_price_gwei, eth_price)

    # Break-even arb notional: the trade size at which the avoided surcharge covers cost.
    rate = prize_rate(gap_ticks, push)
    breakeven = cost["total_usd"] / rate if rate > 0 else float("inf")

    return {
        "gap_ticks": gap_ticks,
        "eth_price": eth_price,
        "ticks_needed": need,
        "feasible": feasible,
        "ticks_pushed": push,
        **cost,
        "surcharge_rate_bps": rate_at_gap(gap_ticks) * 10_000,
        "avoided_bps": rate * 10_000,
        "breakeven_arb_usd": breakeven,
    }


# ---------------------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------------------

def load_from_csv(path):
    """
    Pull (tick, liquidity) pairs from a refbench swap-event CSV. Column names are
    detected rather than assumed, because fetch.py's exact headers may have drifted.
    """
    rows = []
    with open(path) as f:
        rdr = csv.DictReader(f)
        if not rdr.fieldnames:
            return rows
        lower = {c.lower(): c for c in rdr.fieldnames}

        def find(*cands):
            for c in cands:
                if c in lower:
                    return lower[c]
            for c in cands:
                for k, orig in lower.items():
                    if c in k:
                        return orig
            return None

        tcol = find("tick", "tick_after", "post_tick")
        lcol = find("liquidity", "in_range_liquidity", "liq")
        if not tcol or not lcol:
            print(f"  ! {os.path.basename(path)}: could not find tick/liquidity columns "
                  f"in {rdr.fieldnames}", file=sys.stderr)
            return rows

        for r in rdr:
            try:
                t = float(r[tcol]); l = float(r[lcol])
                if l > 0:
                    rows.append((t, l))
            except (TypeError, ValueError):
                continue
    return rows


def percentile(xs, p):
    if not xs:
        return 0.0
    s = sorted(xs)
    k = (len(s) - 1) * p / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return s[int(k)]
    return s[lo] * (hi - k) + s[hi] * (k - lo)


# ---------------------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------------------

def report(liquidity, tick, gas_price_gwei, label):
    print(f"\n{'=' * 78}")
    print(f"{label}")
    print(f"  in-range liquidity L = {liquidity:,.0f}")
    print(f"  tick = {tick:,.0f}   ETH ≈ ${eth_price_from_tick(tick):,.2f}")
    print(f"  gas = {gas_price_gwei} gwei")
    print(f"{'=' * 78}")
    print(f"{'gap':>5} {'push':>5} {'mask':>6} {'push notional':>15} "
          f"{'COST':>9} {'rate':>7} {'avoided':>8} {'break-even arb':>16}")
    print("-" * 82)

    for gap in (66, 80, 100, 114, 150, 200, 400):
        a = analyse(liquidity, tick, gap, gas_price_gwei)
        print(f"{a['gap_ticks']:>5} {a['ticks_pushed']:>5} "
              f"{'full' if a['feasible'] else 'partial':>6} "
              f"${a['push_notional_usd']:>14,.0f} "
              f"${a['total_usd']:>8,.0f} "
              f"{a['surcharge_rate_bps']:>6.2f} "
              f"{a['avoided_bps']:>7.2f} "
              f"${a['breakeven_arb_usd']:>15,.0f}")

    print("-" * 82)
    print("mask    = full: the gap never opens. partial: it opens on a smaller maxAbsGap.")
    print("rate    = surcharge the arbitrageur would have paid, bps.")
    print("avoided = bps actually saved by pushing. For partial masks this is capped at")
    print(f"          {GUARD_MAX_DEV_TICKS - 1} * {CAPTURE_RATE_BPS} / 1e8 = "
          f"{(GUARD_MAX_DEV_TICKS - 1) * CAPTURE_RATE_BPS / 1e4:.2f} bps, whatever the gap.")
    print("break-even arb = notional at which the saving pays for the push.")


def selftest():
    print("Self-test (no network, no data)\n")
    ok = True

    def check(name, got, want, tol=1e-6):
        nonlocal ok
        good = abs(got - want) <= tol * max(1.0, abs(want))
        ok &= good
        print(f"  [{'OK ' if good else 'FAIL'}] {name}: got {got:.10g}, want {want:.10g}")

    check("sqrtPrice at tick 0", sqrt_price_at_tick(0), 1.0)
    check("tick 2 is 1.0001x in price", sqrt_price_at_tick(2) ** 2, 1.0001 ** 2)

    # 49 ticks is 0.49% in price, so ~0.2448% in sqrt-price.
    ratio = sqrt_price_at_tick(49) / sqrt_price_at_tick(0)
    check("sqrt ratio over 49 ticks", ratio, 1.0001 ** 24.5)

    check("mask a 66-tick gap", ticks_needed_to_mask(66), 2)
    check("mask a 114-tick gap", ticks_needed_to_mask(114), 50)   # just out of budget
    check("mask a 60-tick gap", ticks_needed_to_mask(60), 0)      # already invisible

    # Surcharge matches the post-task-9 formula.
    # 100 ticks * 500 bps-per-tick / 1e8 = 5 bps, i.e. $500 on $1M. (My first expected
    # value here was 50 bps — off by 10x. Kept as a reminder that the self-test earns
    # its place: it caught the error before any number reached the appendix.)
    check("surcharge on $1M at 100 ticks", surcharge_avoided_usd(1e6, 100), 1e6 * 0.0005)
    check("surcharge caps at 200bps",      surcharge_avoided_usd(1e6, 10_000), 1e6 * 0.02)

    # Cost scales linearly in L.
    c1 = attack_cost_usd(1e18, 200_000, 49, 10, 3000)["fee_usd"]
    c2 = attack_cost_usd(2e18, 200_000, 49, 10, 3000)["fee_usd"]
    check("fee cost is linear in liquidity", c2 / c1, 2.0)

    print("\n" + ("All self-tests passed." if ok else "SELF-TESTS FAILED."))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", help="refbench swap-event CSV for the 0.01% pool")
    ap.add_argument("--liquidity", type=float, help="in-range L, if you have it directly")
    ap.add_argument("--tick", type=float, default=200_000, help="pool tick (default 200000)")
    ap.add_argument("--gas-gwei", type=float, default=10.0)
    ap.add_argument("--sweep", action="store_true", help="explore without data")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    if args.csv:
        paths = glob.glob(args.csv)
        if not paths:
            print(f"no files matched {args.csv}", file=sys.stderr)
            return 1
        rows = []
        for p in paths:
            rows += load_from_csv(p)
        if not rows:
            print("no usable rows; check the column names printed above", file=sys.stderr)
            return 1

        liqs  = [l for _, l in rows]
        ticks = [t for t, _ in rows]
        med_tick = percentile(ticks, 50)
        print(f"loaded {len(rows):,} swap events from {len(paths)} file(s)")

        # Report at p10 liquidity as well as median: the attacker picks their moment, and
        # the cheapest moment to push is what actually bounds the defence.
        for pct, name in ((10, "p10 liquidity — the attacker's best moment"),
                          (50, "median liquidity")):
            report(percentile(liqs, pct), med_tick, args.gas_gwei, name)
        return 0

    if args.liquidity:
        report(args.liquidity, args.tick, args.gas_gwei, "supplied liquidity")
        return 0

    if args.sweep:
        print("No data supplied — sweeping plausible in-range liquidity.")
        print("Replace this with --csv against refbench data before quoting any number.")
        for L in (1e17, 5e17, 1e18, 5e18, 2e19):
            report(L, args.tick, args.gas_gwei, f"L = {L:.0e} (illustrative)")
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())