"""
Find the block numbers bounding a UTC date range.

    export RPC_URL=...
    python find_blocks.py 2026-08-19 2026-08-21

Prints START_BLOCK / END_BLOCK to paste into config.py, or export directly.

Why binary search rather than arithmetic: estimating from "12 seconds per
block" drifts. Over 600 days a 0.1% error in average block time is ~4,300
blocks — about 14 hours. Binary search against real timestamps is exact and
costs ~25 RPC calls per date.
"""

import sys
from datetime import datetime, timezone

import requests

import config as C


def rpc(method, params):
    r = requests.post(C.RPC_URL, json={"jsonrpc": "2.0", "id": 1,
                                       "method": method, "params": params}, timeout=30)
    j = r.json()
    if "error" in j:
        raise RuntimeError(j["error"])
    return j["result"]


def block_ts(n):
    b = rpc("eth_getBlockByNumber", [hex(n), False])
    if b is None:
        raise RuntimeError(f"block {n} not found")
    return int(b["timestamp"], 16)


def latest_block():
    return int(rpc("eth_blockNumber", []), 16)


def find_block_at(target_ts, lo, hi):
    """First block with timestamp >= target_ts."""
    calls = 0
    while lo < hi:
        mid = (lo + hi) // 2
        calls += 1
        if block_ts(mid) < target_ts:
            lo = mid + 1
        else:
            hi = mid
    return lo, calls


def parse_day(s):
    """'2026-08-19' -> unix timestamp at 00:00:00 UTC."""
    d = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(d.timestamp())


def main():
    if not C.RPC_URL:
        sys.exit("Set RPC_URL first:  export RPC_URL=https://...")
    if len(sys.argv) != 3:
        sys.exit("Usage: python find_blocks.py YYYY-MM-DD YYYY-MM-DD\n"
                 "       (second date is exclusive — use the day AFTER the one you want)")

    start_ts, end_ts = parse_day(sys.argv[1]), parse_day(sys.argv[2])
    if end_ts <= start_ts:
        sys.exit("End date must be after start date.")

    tip = latest_block()
    tip_ts = block_ts(tip)
    print(f"chain tip: block {tip:,}  "
          f"({datetime.fromtimestamp(tip_ts, timezone.utc):%Y-%m-%d %H:%M UTC})\n")

    if start_ts > tip_ts:
        sys.exit("That start date is in the future relative to the chain tip.")

    print(f"searching for {sys.argv[1]} 00:00 UTC ...")
    start_block, c1 = find_block_at(start_ts, 1, tip)
    print(f"searching for {sys.argv[2]} 00:00 UTC ...")
    end_block, c2 = find_block_at(min(end_ts, tip_ts), start_block, tip)

    a, b = block_ts(start_block), block_ts(end_block)
    n = end_block - start_block
    print(f"\nfound in {c1 + c2} calls")
    print(f"  start  {start_block:,}  {datetime.fromtimestamp(a, timezone.utc):%Y-%m-%d %H:%M:%S UTC}")
    print(f"  end    {end_block:,}  {datetime.fromtimestamp(b, timezone.utc):%Y-%m-%d %H:%M:%S UTC}")
    print(f"  span   {n:,} blocks, {(b - a)/3600:.1f} hours, "
          f"{(b - a)/max(n, 1):.2f} s/block avg")

    if n > 15000:
        print(f"\n  note: {n:,} blocks is a lot of eth_getLogs. Expect several")
        print("  minutes and watch your RPC quota. Narrow the range if it stalls.")

    print("\n--- paste into config.py ---\n")
    print(f"START_BLOCK = {start_block}")
    print(f"END_BLOCK   = {end_block}")
    print("\n--- or run without editing ---\n")
    print(f"START_BLOCK={start_block} END_BLOCK={end_block} python fetch.py")
    print(f"START_BLOCK={start_block} END_BLOCK={end_block} python analyze.py")


if __name__ == "__main__":
    main()
