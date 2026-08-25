"""
Configuration for the Backdraft reference-price benchmark.

Everything you might want to change lives here.
"""

import os

# ---------------------------------------------------------------- RPC

RPC_URL = os.environ.get("RPC_URL", "")   # export RPC_URL=https://eth-mainnet.g.alchemy.com/v2/KEY

# eth_getLogs block range per request. Lower this if your provider complains.
LOG_CHUNK = 2000

# ---------------------------------------------------------------- Block range
#
# Pick a window with real volatility in it — a flat week teaches you nothing.
# ~7200 blocks = 1 day. Start with 1 day, widen once it works.

START_BLOCK = int(os.environ.get("START_BLOCK", 21_500_000))
END_BLOCK   = int(os.environ.get("END_BLOCK",   21_507_200))

# ---------------------------------------------------------------- Pools
#
# Uniswap v3 ETH/USDC. token0 = USDC (6dp), token1 = WETH (18dp) in all three,
# because 0xA0b8... < 0xC02a... so USDC sorts first.
#
# `label` is what shows up in the output table.

POOLS = [
    {"label": "v3_001", "address": "0xe0554a476a092703abdb3ef35c80e0d76d32939f", "fee_bps": 1},
    {"label": "v3_005", "address": "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640", "fee_bps": 5},
    {"label": "v3_030", "address": "0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8", "fee_bps": 30},
    {"label": "v3_100", "address": "0x7bea39867e4169dbe237d55c8242a8f2fcdcc387", "fee_bps": 100},
]

# Which pool plays the role of "our v4 pool" for the own-EMA baseline.
#
# We have no Backdraft pool deployed, so we borrow an existing pool's tick
# history as a stand-in. Use the THINNEST pool (1% fee) — it's the closest
# available proxy for a freshly launched v4 pool: less flow, laggier price.
#
# Even this flatters the EMA design. A real day-one Backdraft pool would see
# far less flow than the v3 1% pool, so its own-history EMA would be staler
# still. Treat the measured EMA error as a LOWER BOUND on how bad it gets.
TARGET_POOL = "v3_100"

# Simulate thin flow: let the EMA update on only this fraction of blocks,
# modelling a new pool where most blocks contain no swap at all.
# 1.0 = update every block (optimistic). 0.2 = a swap in 1 block out of 5.
# Set to a list to compare several flow regimes in one run.
EMA_FLOW_RATES = [1.0, 0.3, 0.1]

DEC0 = 6    # USDC
DEC1 = 18   # WETH

# Swap(address,address,int256,int256,uint160,uint128,int24)
SWAP_TOPIC0 = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"

# ---------------------------------------------------------------- Ground truth

BINANCE_SYMBOL   = os.environ.get("BINANCE_SYMBOL", "ETHUSDC")   # ETHUSDT if USDC pair is thin
BINANCE_INTERVAL = "1m"
BINANCE_BASE     = "https://api.binance.com/api/v3/klines"

# ---------------------------------------------------------------- Methods under test

EMA_ALPHA_BPS = 200          # own-pool EMA damping, 200 = 2% per block with a swap
TWAP_WINDOWS  = [300, 1800]  # seconds: 5 min, 30 min
GUARD_MAX_DEV_TICKS = 50     # freeze if |composite_spot - composite_twap| exceeds this

# ---------------------------------------------------------------- Output

DATA_DIR = "data"
OUT_DIR  = "out"
