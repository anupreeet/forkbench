# forkbench results

`reset` = time until the task container accepts commands (provision + image pull + container start). Median (min–max, n).

| task | strategy | reset (median, range, n) | provision | solve | verify | correct | verifier failed | errors | est. cost |
|---|---|---|---|---|---|---|---|---|---|
| regex-log | checkpoint | 75.6s (75.6–75.6, n=1) | 73.0s (73.0–73.0, n=1) | 4.3s (4.3–4.3, n=1) | 98.0s (98.0–98.0, n=1) | 2/2 | 0 | 0 | $0.041 |
| regex-log | scratch | 116.7s (116.7–116.7, n=1) | 73.0s (73.0–73.0, n=1) | 4.2s (4.2–4.2, n=1) | 326.6s (326.6–326.6, n=1) | 1/2 | 0 | 0 | $0.057 |

## One-off checkpoint cost per task

| task | golden build + checkpoint | break-even trials |
|---|---|---|
| regex-log | 220s (checkpoint 70s) | 5.3 |

## Correctness and isolation checks

**checkpoint**

- trials checked: 3
- no leftover state: 3/3
- max |clock skew|: 0.25s
- no-op exec round trip (median): 1.05s
- container had to be restarted: 0/3
- unique boot_id / machine-id / hostname / fresh uuid: 1/3 / 1/3 / 1/3 / 3/3
- egress (want → result, time to take effect): deny_all_at_restore: BLOCKED NOT reached (last {'host': 'BLOCKED(000)', 'container': 'OPEN'}) after 120.8s, after_change_to_open: OPEN ok after 25.5s

**scratch**

- trials checked: 3
- no leftover state: 3/3
- max |clock skew|: 0.29s
- no-op exec round trip (median): 1.03s
- container had to be restarted: n/a
- unique boot_id / machine-id / hostname / fresh uuid: 3/3 / 3/3 / 1/3 / 3/3
- egress (want → result, time to take effect): open_before: OPEN NOT reached (last {'host': 'BLOCKED(000)', 'container': 'OPEN'}) after 7.3s, after_change_to_deny_all: BLOCKED NOT reached (last {'host': 'BLOCKED(000)', 'container': 'OPEN'}) after 122.7s

