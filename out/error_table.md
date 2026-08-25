| Method | Mean err (bps) | Median | p95 | Max | Lag (blocks) | Coverage |
|---|---|---|---|---|---|---|
| `spot_v3_005` | 3.77 | 3.3 | 8.54 | 39.43 | 4 | 100.0% |
| `composite_median_guarded` | 3.8 | 3.31 | 8.63 | 38.8 | 0 | 99.2% |
| `composite_median` | 3.81 | 3.3 | 8.63 | 39.43 | 4 | 100.0% |
| `composite_mean` | 4.29 | 3.64 | 10.06 | 42.6 | 4 | 100.0% |
| `twap_300s_v3_005` | 5.89 | 4.88 | 14.74 | 54.28 | 21 | 100.0% |
| `twap_1800s_v3_005` | 10.88 | 9.3 | 26.1 | 71.01 | 25 | 100.0% |
| `own_pool_ema` | 44.05 | 41.23 | 93.32 | 146.56 | 23 | 100.0% |
| `own_pool_ema_flow0.3` | 46.6 | 45.55 | 96.98 | 163.49 | 25 | 100.0% |
| `own_pool_ema_flow0.1` | 52.23 | 53.92 | 105.7 | 169.35 | 11 | 100.0% |

Guard freeze rate: 0.76% of blocks (threshold 50 ticks)
Window: blocks 21500002–21507200, 7199 blocks, ETHUSDC as ground truth