# forkbench results

`reset` = time until the task container accepts commands (provision + image pull + container start). Median (min–max, n).

| task | strategy | reset (median, range, n) | provision | solve | verify | correct | verifier failed | errors | est. cost |
|---|---|---|---|---|---|---|---|---|---|
| build-cython-ext | checkpoint | 78.3s (71.9–124.7, n=4) | 75.3s (69.4–122.5, n=4) | 344.2s (339.6–369.7, n=4) | 324.9s (323.3–328.4, n=4) | 0/0 | 5 | 1 | $0.186 |
| build-cython-ext | scratch | 140.8s (136.9–216.2, n=5) | 75.0s (69.4–143.2, n=5) | 350.6s (348.1–367.5, n=5) | 325.4s (65.6–362.0, n=5) | 0/2 | 4 | 0 | $0.228 |
| chess-best-move | checkpoint | 81.6s (75.6–97.9, n=4) | 79.1s (73.0–95.5, n=4) | 41.7s (40.8–63.8, n=4) | 323.4s (319.4–332.6, n=4) | 0/0 | 5 | 1 | $0.133 |
| chess-best-move | scratch | 209.3s (144.1–232.1, n=5) | 94.7s (62.2–116.2, n=5) | 59.4s (38.4–62.7, n=5) | 329.6s (319.2–336.1, n=5) | 0/0 | 6 | 0 | $0.175 |
| qemu-startup | checkpoint | 110.6s (73.3–128.3, n=5) | 108.3s (70.8–126.1, n=5) | 39.1s (38.8–39.4, n=5) | 6.4s (4.8–6.8, n=5) | 0/0 | 6 | 0 | $0.048 |
| qemu-startup | scratch | 141.4s (131.4–157.3, n=5) | 76.0s (71.5–83.8, n=5) | 39.5s (38.4–39.7, n=5) | 5.4s (5.2–5.8, n=5) | 0/0 | 6 | 0 | $0.064 |
| regex-log | checkpoint | 89.5s (76.8–130.0, n=5) | 87.2s (74.0–127.8, n=5) | 4.4s (4.1–5.2, n=5) | 329.3s (80.5–333.7, n=5) | 1/1 | 4 | 1 | $0.120 |
| regex-log | scratch | 118.8s (110.4–168.7, n=5) | 77.9s (69.7–102.6, n=5) | 4.4s (4.2–5.0, n=5) | 325.2s (322.7–328.7, n=5) | 0/0 | 6 | 0 | $0.143 |

## One-off checkpoint cost per task

| task | golden build + checkpoint | break-even trials |
|---|---|---|
| qemu-startup | 263s (checkpoint 107s) | 8.5 |
| build-cython-ext | 227s (checkpoint 82s) | 3.6 |
| chess-best-move | 227s (checkpoint 72s) | 1.8 |
| regex-log | 219s (checkpoint 99s) | 7.5 |

## Correctness and isolation checks

**checkpoint**

- trials checked: 25
- no leftover state: 25/25
- max |clock skew|: 0.34s
- no-op exec round trip (median): 1.05s
- container had to be restarted: 0/25
- unique boot_id / machine-id / hostname / fresh uuid: 4/25 / 4/25 / 1/25 / 25/25
- egress (want → result, time to take effect): deny_all_at_restore: BLOCKED ok after 2.4s, after_change_to_open: OPEN ok after 25.5s; deny_all_at_restore: BLOCKED ok after 2.6s, after_change_to_open: OPEN ok after 34.7s; deny_all_at_restore: BLOCKED ok after 2.5s, after_change_to_open: OPEN ok after 20.6s; deny_all_at_restore: BLOCKED ok after 2.2s, after_change_to_open: OPEN ok after 24.8s

**scratch**

- trials checked: 28
- no leftover state: 28/28
- max |clock skew|: 0.35s
- no-op exec round trip (median): 1.05s
- container had to be restarted: n/a
- unique boot_id / machine-id / hostname / fresh uuid: 28/28 / 28/28 / 1/28 / 28/28
- egress (want → result, time to take effect): open_before: OPEN ok after 7.4s, after_change_to_deny_all: BLOCKED ok after 19.3s; open_before: OPEN ok after 8.9s, after_change_to_deny_all: BLOCKED ok after 16.8s; open_before: OPEN ok after 8.2s, after_change_to_deny_all: BLOCKED ok after 27.5s; open_before: OPEN ok after 3.7s, after_change_to_deny_all: BLOCKED ok after 22.4s

