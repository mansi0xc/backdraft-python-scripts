"""Verify the pure math with synthetic data. No network needed."""
import numpy as np, pandas as pd, math, sys
import config as C
from analyze import (tick_to_price, price_to_tick, weighted_median,
                     time_weighted_average, ema_series)
from fetch import decode_swap, to_signed

ok = True
def check(name, cond, detail=""):
    global ok
    print(f"  [{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond: ok = False

print("tick <-> price roundtrip")
for p in [1500.0, 3000.0, 4237.55]:
    t = price_to_tick(p); back = tick_to_price(t)
    check(f"${p}", abs(back-p) < 0.01, f"tick={t:.1f} back=${back:.4f}")
check("ETH $3000 lands near real v3 ticks (~196k)", 195000 < price_to_tick(3000.0) < 198000)

print("\n1 tick ~= 1 bp")
t = price_to_tick(3000.0)
drift = abs((tick_to_price(t+1)/3000.0 - 1)*10000)
check("one tick moves price ~1bp", 0.95 < drift < 1.05, f"{drift:.4f} bps")

print("\nweighted median resists a corrupted source")
vals = np.array([[100.0, 101.0, 9999.0]])       # third pool lying wildly
wts  = np.array([[50.0,   40.0,   10.0]])       # and it's the smallest
med  = weighted_median(vals, wts)[0]
mean = np.sum(vals*wts)/np.sum(wts)
check("median ignores the liar", med in (100.0, 101.0), f"median={med}")
check("mean is dragged", mean > 1000, f"mean={mean:.1f}")

print("\nweighted median needs >50% weight to move")
vals = np.array([[100.0, 100.0, 500.0]])
for w3, expect in [(200.0, 500.0), (90.0, 100.0), (10.0, 100.0)]:
    wts = np.array([[50.0, 50.0, w3]])
    m = weighted_median(vals, wts)[0]
    share = w3/(100.0+w3)
    check(f"attacker weight {share:.0%}", abs(m-expect) < 1e-9, f"-> {m}")

print("\ntime-weighted average")
ts = np.arange(0, 1200, 12)
flat = np.full(len(ts), 200.0)
check("flat series unchanged", np.allclose(time_weighted_average(ts, flat, 300), 200.0))
step = np.concatenate([np.full(50, 100.0), np.full(50, 200.0)])
tw = time_weighted_average(np.arange(0,1200,12), step, 300)
check("TWAP lags a step change", tw[55] < 200.0 and tw[-1] > tw[55], f"tw[55]={tw[55]:.1f}")

print("\nEMA baseline")
e = ema_series(np.full(100, 500.0), 200, 1.0)
check("EMA converges on constant input", abs(e[-1]-500.0) < 1e-6)
jump = np.concatenate([np.full(10, 100.0), np.full(90, 200.0)])
e2 = ema_series(jump, 200, 1.0)
check("EMA of own history lags badly (the bug we're proving)", e2[15] < 120.0,
      f"after 5 blocks at 200, EMA={e2[15]:.1f}")

print("\nswap event decoding")
def word(v): return (v & (1<<256)-1).to_bytes(32,'big')
payload = "0x" + (word(-1500000000) + word(500000000000000000) +
                  word(1234567890123456789012345) + word(9876543210) + word(196257)).hex()
d = decode_swap(payload)
check("amount0 negative", d["amount0"] == -1500000000, str(d["amount0"]))
check("amount1 positive", d["amount1"] == 500000000000000000)
check("tick", d["tick"] == 196257, str(d["tick"]))
d2 = decode_swap("0x" + (word(0)+word(0)+word(0)+word(0)+word(-887272)).hex())
check("negative tick", d2["tick"] == -887272, str(d2["tick"]))

print("\nend-to-end on synthetic market data")
n = 600
rng = np.random.default_rng(7)
truth = 196260 + np.cumsum(rng.normal(0, 3, n))          # CEX random walk
lagged = pd.Series(truth).shift(3).bfill().to_numpy() + rng.normal(0,1,n)  # pool lags
ts = np.arange(n)*12
own_ema = ema_series(lagged, 200, 1.0)
err_ema  = np.nanmean(np.abs(own_ema - truth))
err_spot = np.nanmean(np.abs(lagged  - truth))
check("own-EMA is worse than raw spot", err_ema > err_spot,
      f"ema={err_ema:.1f}bps spot={err_spot:.1f}bps")

print("\n" + ("ALL PASS" if ok else "FAILURES ABOVE"))
sys.exit(0 if ok else 1)
