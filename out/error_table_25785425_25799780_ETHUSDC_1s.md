| Method | Mean err (bps) | Median | p95 | Max | Lag (blocks) | Coverage |
|---|---|---|---|---|---|---|
| `shipped_fast_spot_frozen` | 2.69 | 1.68 | 8.55 | 70.97 | 19 | 89.9% |
| `shipped_fast_spot_raw` | 3.48 | 1.81 | 11.25 | 228.65 | 0 | 100.0% |
| `composite_median_guarded` | 3.5 | 3.03 | 8.15 | 57.97 | 19 | 89.9% |
| `spot_v3_005` | 4.18 | 3.15 | 9.88 | 228.65 | 0 | 100.0% |
| `composite_median` | 4.18 | 3.15 | 9.88 | 228.65 | 0 | 100.0% |
| `composite_mean` | 4.45 | 3.33 | 10.79 | 227.34 | 0 | 100.0% |
| `twap_300s_v3_005` | 9.41 | 6.03 | 26.21 | 376.67 | 10 | 100.0% |
| `twap_1800s_v3_005` | 25.18 | 13.55 | 76.94 | 639.0 | 7 | 100.0% |
| `own_pool_ema` | 58.23 | 53.72 | 115.52 | 564.01 | 14 | 100.0% |
| `own_pool_ema_flow0.3` | 72.78 | 58.53 | 192.55 | 759.53 | 2 | 100.0% |
| `own_pool_ema_flow0.1` | 105.57 | 70.02 | 417.34 | 971.95 | 16 | 100.0% |

Guard freeze rate: 10.06% of blocks (threshold 50 ticks)
Window: blocks 25785425–25799780, 14356 blocks, ETHUSDC 1s as ground truth