# GPU retime 2026-10-19 (after CUDA restore)

| Config | n | Median s | p90 | Δ vs base |
|---|---:|---:|---:|---:|
| base-560 | 19 | 8.58 | 9.54 | +0.00 |
| v3_lean-560 | 19 | 9.27 | 10.51 | +0.69 |
| base-1024 | 19 | 13.89 | 14.17 | +0.00 |
| v3_lean-1024 | 19 | 14.62 | 15.37 | +0.73 |

gr3d_max=99
pct_samples_ge_50pct=0.873
n=1743

Compared with Feature 9 run earlier today (17:17–17:50 EEST, before the 18:22 reboot): base-560 8.52 / v3_lean-560 9.36 (+0.84); base-1024 13.95 / v3_lean-1024 14.78 (+0.83). Retime confirms those were already GPU timings.
