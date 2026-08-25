# Reference-price benchmark

How wrong is each on-chain price reference, in basis points against Binance, and how fast does it react?

Every arbitrage-capture hook needs a reference price, and every one that exists reaches off-chain to get it — Pyth, Chainlink, L1SLOAD, a second pool. Nobody has published what the on-chain options actually cost you in accuracy. This measures it.

## Run it

```bash
pip install -r requirements.txt
export RPC_URL=https://eth-mainnet.g.alchemy.com/v2/YOUR_KEY

python selftest.py      # verifies the math, no network needed
python fetch.py         # ~2-5 min for one day of blocks
python analyze.py
```

Outputs land in `out/`: `results.csv`, `error_table.md` (paste straight into the README), and three PNGs.

## Choosing a window

Defaults to 7,200 blocks ≈ 1 day. **Pick a day with a real move in it** — a flat day makes every method look identical and teaches you nothing. Find a day ETH moved 5%+ and use that. Then run a calm day too; the comparison between the two is itself a result.

Override without editing files:

```bash
START_BLOCK=21500000 END_BLOCK=21507200 python fetch.py
```

Data is cached per-file in `data/`. Delete a file to refetch it.

## What each method is

| Method | What it is |
|---|---|
| `own_pool_ema` | EMA of our own pool's tick. The design we abandoned — included to prove why. |
| `spot_v3_005` | Current tick of the deepest v3 pool. Simplest thing that could work. |
| `twap_300s` / `twap_1800s` | Time-weighted average over 5 / 30 minutes. |
| `composite_mean` | Liquidity-weighted mean across all three v3 fee tiers. Alex's suggestion. |
| `composite_median` | Liquidity-weighted **median**. Same inputs, needs >50% of weight to corrupt. |
| `composite_median_guarded` | Median, frozen when spot and TWAP disagree beyond threshold. |

## Reading the results

**If `spot_v3_005` is within ~1 bp of `composite_median`:** the composite isn't buying you anything on this pair. Ship the simple version and say so — a measured negative result is still a result, and it saves you a week.

**If `own_pool_ema` is dramatically worse:** that's the empirical proof that a pool can't detect its own staleness from its own history. It's the strongest single chart you'll produce.

**If every method has large errors during the volatile window:** all on-chain references lag CEX by construction. That's the honest limit of the approach and should go in your limitations section rather than being hidden.

`lag_blocks` is how many blocks behind Binance a method reacts, found by cross-correlating changes. Lower is better. Expect TWAPs to be worst.

## Caveats to state when you publish

- **Ground truth is Binance ETHUSDC**, not the platonic price. Other venues differ by a few bps.
- **Block timestamps are interpolated** from the endpoints at ~12s/block rather than fetched per block. Fine for joining to 1-minute candles, wrong if you need sub-block precision.
- **Liquidity as weight** uses v3's in-range `liquidity` (virtual L), which is a depth proxy, not TVL. Reasonable for weighting; not the same thing.
- **Gas is not measured here.** This is off-chain analysis. Measure gas separately in Foundry and put both numbers in the same table — accuracy is only half the frontier.
- **One pair, one window.** Don't generalise to thin pairs; if anything they're where on-chain references get worse.
