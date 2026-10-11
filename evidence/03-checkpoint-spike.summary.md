# Checkpoint spike: what a Runta checkpoint preserves (2026-10-11 02:03–02:11 UTC)

Raw log: `03-checkpoint-spike.progress.log`. Script: `scripts/spike_checkpoint.py`.
runta CLI 0.2.15, runta-sdk 0.2.1, runtime 2 vCPU / 4096 MiB. Image: regex-log:2.0 (29 MB compressed, 118 MB on disk).

| Step | Seconds (n=1) |
|---|---|
| create source runtime (wait=True) | 78.3 |
| docker pull regex-log (inside runtime, from ghcr.io) | 41.6 |
| checkpoint create (wait until READY) | 62.9 |
| delete source | 1.7 |
| restore copy A (after source deleted) | 76.2 |
| restore copy B (2nd restore, same checkpoint) | 77.1 |

| Check | Source (after ckpt) | Copy A | Copy B |
|---|---|---|---|
| file written before checkpoint | ✔ | ✔ | ✔ |
| Docker daemon running | ✔ | ✔ | ✔ |
| task image present (no re-pull) | ✔ | ✔ | ✔ |
| task container still running | ✔ "Up About a minute" | ✔ "Up 3 minutes" | ✔ "Up 5 minutes" |
| file inside container | ✔ | ✔ | ✔ |
| outbound HTTPS (example.com) | 200 | 200 | 200 |
| file written in copy A visible in copy B | – | – | ✘ absent (isolated ✔) |
| machine-id | b043…df | same | same |
| source still RUNNING after checkpoint | ✔ | | |

Conclusions:
1. A checkpoint preserves the filesystem **and running processes**: the Docker container kept running across checkpoint → restore. This matches `/docs/runtime/checkpoints/` ("filesystem and running processes").
2. Restores work after the source runtime is deleted, and copies are isolated from each other.
3. Restore time (76–77 s) ≈ fresh create time (73–78 s). The checkpoint saves the *environment setup* (pull + container start), not machine provisioning.
4. Copies share machine-id/hostname (expected for snapshot clones; recorded, not judged).

Method notes:
- The `nohup … &` counter process started inside one exec call was already gone before the checkpoint, so it says nothing about checkpoints. Background-process behavior was tested separately (L-21, FEEDBACK bad #6).
- The script's cleanup step errored after the last measurement; the remaining copy and the checkpoint were deleted manually at 02:11Z. Fixed in `scripts/spike_checkpoint.py`.
