"""
Find reference pools from the Uniswap v3 factory, so no pool address in this
repo comes from anyone's memory.

    export RPC_URL=...
    python discover.py

Prints a POOLS block you paste into config.py, with live liquidity for each
tier so you can see which sources are worth including.

Why this exists: hardcoded pool addresses are a silent failure mode. A wrong
address gives you an empty log query and a benchmark that quietly measures
nothing. Resolving them from the factory makes that impossible.
"""

import sys

import requests

import config as C

# Uniswap v3 factory, same address on mainnet and most L2s.
V3_FACTORY = "0x1F98431c8aD98523631AE4a59f267346ea31F984"

# Verified by keccak, not recalled:
#   getPool(address,address,uint24) -> 0x1698ee82
#   slot0()      -> 0x3850c7bd
#   liquidity()  -> 0x1a686502
#   token0()     -> 0x0dfe1681
#   decimals()   -> 0x313ce567
SEL_GET_POOL  = "1698ee82"
SEL_SLOT0     = "3850c7bd"
SEL_LIQUIDITY = "1a686502"
SEL_TOKEN0    = "0dfe1681"
SEL_DECIMALS  = "313ce567"

# All v3 fee tiers. 0.01% was added by governance after launch and is often
# forgotten — include it and let the data decide whether it's useful.
V3_FEE_TIERS = [
    (100,   "v3_001", "0.01%"),
    (500,   "v3_005", "0.05%"),
    (3000,  "v3_030", "0.30%"),
    (10000, "v3_100", "1.00%"),
]

# Change these two to benchmark a different pair.
TOKEN_A = ("USDC", "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48")
TOKEN_B = ("WETH", "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2")

ZERO = "0x" + "0" * 40


def rpc(method, params):
    r = requests.post(C.RPC_URL, json={"jsonrpc": "2.0", "id": 1,
                                       "method": method, "params": params}, timeout=30)
    j = r.json()
    if "error" in j:
        raise RuntimeError(j["error"])
    return j["result"]


def call(to, data):
    return rpc("eth_call", [{"to": to, "data": data}, "latest"])


def pad_addr(a):
    return a.lower().replace("0x", "").rjust(64, "0")


def pad_uint(n):
    return hex(n)[2:].rjust(64, "0")


def get_pool(token_a, token_b, fee):
    data = "0x" + SEL_GET_POOL + pad_addr(token_a) + pad_addr(token_b) + pad_uint(fee)
    res = call(V3_FACTORY, data)
    return "0x" + res[-40:]


def get_liquidity(pool):
    return int(call(pool, "0x" + SEL_LIQUIDITY), 16)


def get_tick(pool):
    """slot0() returns (sqrtPriceX96, tick, ...) — tick is the second word."""
    res = call(pool, "0x" + SEL_SLOT0)
    body = res[2:]
    raw = int(body[64:128], 16)
    return raw - (1 << 256) if raw >= (1 << 255) else raw


def get_token0(pool):
    return "0x" + call(pool, "0x" + SEL_TOKEN0)[-40:]


def get_decimals(token):
    return int(call(token, "0x" + SEL_DECIMALS), 16)


def main():
    if not C.RPC_URL:
        sys.exit("Set RPC_URL first:  export RPC_URL=https://...")

    (name_a, addr_a), (name_b, addr_b) = TOKEN_A, TOKEN_B
    print(f"Pair: {name_a} / {name_b}\n")

    found = []
    for fee, label, pretty in V3_FEE_TIERS:
        pool = get_pool(addr_a, addr_b, fee)
        if pool == ZERO:
            print(f"  {pretty:>6}  no pool")
            continue
        try:
            liq = get_liquidity(pool)
            tick = get_tick(pool)
        except Exception as e:
            print(f"  {pretty:>6}  {pool}  unreadable ({e})")
            continue
        print(f"  {pretty:>6}  {pool}  L={liq:>26,}  tick={tick}")
        found.append({"label": label, "address": pool, "fee_bps": fee // 100,
                      "liquidity": liq, "tick": tick})

    if not found:
        sys.exit("\nNo pools found. Check the token addresses.")

    # token ordering determines the decimal scaling in analyze.py
    t0 = get_token0(found[0]["address"])
    d0 = get_decimals(t0)
    other = addr_b if t0.lower() == addr_a.lower() else addr_a
    d1 = get_decimals(other)
    t0_name = name_a if t0.lower() == addr_a.lower() else name_b

    total = sum(f["liquidity"] for f in found)
    print(f"\ntoken0 = {t0_name} ({t0})  DEC0={d0}  DEC1={d1}")

    print("\nLiquidity share — anything under ~1% contributes almost nothing")
    print("to a liquidity-weighted median, but still costs a full read:")
    for f in found:
        share = 100 * f["liquidity"] / total if total else 0
        flag = "  <- marginal" if share < 1 else ""
        print(f"  {f['label']:>8}  {share:5.1f}%{flag}")

    spread = max(f["tick"] for f in found) - min(f["tick"] for f in found)
    print(f"\nCurrent tick spread across tiers: {spread} ticks (~{spread} bps)")
    if abs(spread) < 5:
        print("  Tiers agree closely right now — the composite may add little.")
        print("  The benchmark will tell you whether that holds under volatility.")

    print("\n--- paste into config.py ---\n")
    print("POOLS = [")
    for f in found:
        print(f'    {{"label": "{f["label"]}", '
              f'"address": "{f["address"]}", "fee_bps": {f["fee_bps"]}}},')
    print("]")
    print(f"\nDEC0 = {d0}\nDEC1 = {d1}")

    thinnest = min(found, key=lambda f: f["liquidity"])
    print(f'TARGET_POOL = "{thinnest["label"]}"   '
          f'# thinnest = closest proxy for a new v4 pool')


if __name__ == "__main__":
    main()
