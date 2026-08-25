"""
Pull the raw data: Uniswap v3 Swap events + Binance klines.

    export RPC_URL=https://eth-mainnet.g.alchemy.com/v2/ouigpC_utbObH4NDiyunfv1nOUt8qQv8
    python fetch.py

Writes data/swaps_<label>.csv and data/binance.csv.
Caches per-chunk, so a crash mid-run doesn't lose everything — just rerun.

Why Swap events instead of reading slot0 per block:
one eth_getLogs call covers thousands of blocks, and every Swap event carries
the post-swap tick, sqrtPriceX96 and in-range liquidity. Reading slot0 for
every block would be one RPC call per pool per block — tens of thousands of
calls and hours of waiting.
"""

import json
import os
import sys
import time

import pandas as pd
import requests

import config as C


# ------------------------------------------------------------------ helpers

def rpc(method, params, retries=5):
    """Single JSON-RPC call with backoff on rate limits."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    for attempt in range(retries):
        try:
            r = requests.post(C.RPC_URL, json=payload, timeout=60)
            j = r.json()
            if "error" in j:
                msg = str(j["error"])
                # range-too-large / rate limit -> let caller shrink and retry
                raise RuntimeError(msg)
            return j["result"]
        except Exception as e:
            if attempt == retries - 1:
                raise
            wait = 2 ** attempt
            print(f"    retry {attempt+1}/{retries} in {wait}s  ({e})")
            time.sleep(wait)


def to_signed(raw: int, bits: int) -> int:
    """Two's-complement decode for int256 / int24 packed into 32 bytes."""
    if raw >= (1 << (bits - 1)):
        raw -= (1 << bits)
    return raw


def decode_swap(data_hex: str) -> dict:
    """
    Swap event non-indexed payload, 5 words of 32 bytes:
      amount0 (int256), amount1 (int256), sqrtPriceX96 (uint160),
      liquidity (uint128), tick (int24)
    """
    b = bytes.fromhex(data_hex[2:])
    if len(b) < 160:
        raise ValueError(f"short swap payload: {len(b)} bytes")
    w = [int.from_bytes(b[i * 32:(i + 1) * 32], "big") for i in range(5)]
    return {
        "amount0":      to_signed(w[0], 256),
        "amount1":      to_signed(w[1], 256),
        "sqrtPriceX96": w[2],
        "liquidity":    w[3],
        "tick":         to_signed(w[4], 256),   # int24 sign-extended into the word
    }


def block_timestamp(block_number: int) -> int:
    res = rpc("eth_getBlockByNumber", [hex(block_number), False])
    return int(res["timestamp"], 16)


# ------------------------------------------------------------------ pools

def fetch_pool(pool: dict) -> pd.DataFrame:
    label = pool["label"]
    path = os.path.join(C.DATA_DIR, f"swaps_{label}.csv")
    if os.path.exists(path):
        print(f"  {label}: cached -> {path}")
        return pd.read_csv(path)

    rows = []
    start = C.START_BLOCK
    chunk = C.LOG_CHUNK

    while start <= C.END_BLOCK:
        end = min(start + chunk - 1, C.END_BLOCK)
        try:
            logs = rpc("eth_getLogs", [{
                "address":   pool["address"],
                "topics":    [C.SWAP_TOPIC0],
                "fromBlock": hex(start),
                "toBlock":   hex(end),
            }])
        except RuntimeError as e:
            # provider rejected the range — halve it and retry the same start
            if chunk > 100:
                chunk //= 2
                print(f"    range rejected, shrinking chunk to {chunk}")
                continue
            raise

        for lg in logs:
            d = decode_swap(lg["data"])
            d["block"] = int(lg["blockNumber"], 16)
            d["logIndex"] = int(lg["logIndex"], 16)
            rows.append(d)

        pct = 100 * (end - C.START_BLOCK + 1) / (C.END_BLOCK - C.START_BLOCK + 1)
        print(f"  {label}: blocks {start}-{end}  ({len(rows)} swaps, {pct:.0f}%)")
        start = end + 1

    df = pd.DataFrame(rows)
    if df.empty:
        print(f"  !! {label}: no swaps found. Check the address and block range.")
        return df

    df = df.sort_values(["block", "logIndex"]).reset_index(drop=True)
    os.makedirs(C.DATA_DIR, exist_ok=True)
    df.to_csv(path, index=False)
    print(f"  {label}: {len(df)} swaps -> {path}")
    return df


def build_block_times() -> pd.DataFrame:
    """
    Map block number -> unix timestamp.

    Fetching every block header is slow, so we anchor on the endpoints and
    interpolate. Post-merge slots are a fixed 12s, so drift over a day is
    seconds — irrelevant when joining to 1-minute klines.
    """
    path = os.path.join(C.DATA_DIR, "block_times.csv")
    if os.path.exists(path):
        return pd.read_csv(path)

    print("  anchoring block timestamps...")
    t0 = block_timestamp(C.START_BLOCK)
    t1 = block_timestamp(C.END_BLOCK)
    span = C.END_BLOCK - C.START_BLOCK
    per_block = (t1 - t0) / span
    print(f"  {t0} -> {t1}   ({per_block:.2f} s/block)")

    blocks = range(C.START_BLOCK, C.END_BLOCK + 1)
    df = pd.DataFrame({
        "block": list(blocks),
        "timestamp": [int(t0 + per_block * (b - C.START_BLOCK)) for b in blocks],
    })
    os.makedirs(C.DATA_DIR, exist_ok=True)
    df.to_csv(path, index=False)
    return df


# ------------------------------------------------------------------ binance

def fetch_binance(start_ts: int, end_ts: int) -> pd.DataFrame:
    path = os.path.join(C.DATA_DIR, "binance.csv")
    if os.path.exists(path):
        print(f"  binance: cached -> {path}")
        return pd.read_csv(path)

    rows = []
    cur = start_ts * 1000
    end_ms = end_ts * 1000

    while cur < end_ms:
        r = requests.get(C.BINANCE_BASE, params={
            "symbol":    C.BINANCE_SYMBOL,
            "interval":  C.BINANCE_INTERVAL,
            "startTime": cur,
            "endTime":   end_ms,
            "limit":     1000,
        }, timeout=30)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        for k in batch:
            rows.append({
                "timestamp": k[0] // 1000,     # open time, seconds
                "open":  float(k[1]),
                "high":  float(k[2]),
                "low":   float(k[3]),
                "close": float(k[4]),
            })
        cur = batch[-1][0] + 60_000
        print(f"  binance: {len(rows)} candles")
        time.sleep(0.2)

    df = pd.DataFrame(rows).drop_duplicates("timestamp").sort_values("timestamp")
    os.makedirs(C.DATA_DIR, exist_ok=True)
    df.to_csv(path, index=False)
    print(f"  binance: {len(df)} candles -> {path}")
    return df


# ------------------------------------------------------------------ main

def main():
    if not C.RPC_URL:
        sys.exit("Set RPC_URL first:  export RPC_URL=https://...")

    os.makedirs(C.DATA_DIR, exist_ok=True)

    print(f"Blocks {C.START_BLOCK} -> {C.END_BLOCK}  "
          f"({C.END_BLOCK - C.START_BLOCK + 1} blocks, ~{(C.END_BLOCK-C.START_BLOCK)*12/3600:.1f}h)")

    print("\nBlock timestamps")
    bt = build_block_times()

    print("\nPools")
    for pool in C.POOLS:
        fetch_pool(pool)

    print("\nBinance")
    fetch_binance(int(bt.timestamp.iloc[0]), int(bt.timestamp.iloc[-1]) + 120)

    print("\nDone. Now run:  python analyze.py")


if __name__ == "__main__":
    main()
