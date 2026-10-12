# Reproduce the experiments

1. From the submission directory, start the course container:

   ```bash
   docker run --rm -it -v "$PWD":/work -w /work ghcr.io/yeonjong21/cs30401-lab1:2026f-v1 bash
   ```

2. Inside the container, apply the patch and run:

   ```bash
   bash code/apply_patch.sh
   python3 code/run_experiment.py
   ```

   This runs 33 experiments: 11 conditions, each with CUBIC/CubicNew/BBR.
   Each simulation lasts 200 s.
   Scenarios, plots, and scores are generated automatically.

   | Purpose | Capacity | Outage (ms) | Flows | Runs |
   | --- | --- | --- | --- | --- |
   | Restart cost | Constant | 0 | 1 | 3 |
   | Outage duration | Constant | 30, 100, 200 | 1 | 9 |
   | Capacity change | Drop, rise | 0 | 1 | 6 |
   | Combined effects | Drop, rise | 100 | 1 | 6 |
   | Fairness | Constant, drop, rise | 100 | 4 | 9 |

   By default, two experiments run in parallel. To use eight:

   ```bash
   python3 code/run_experiment.py --jobs 8
   ```

3. Open `results/experiments/` on your host computer:

   | File | Contents |
   | --- | --- |
   | `<run>/overview.png` | Handover, congestion, loss, and TCP plots |
   | `metrics/summary.csv` | Goodput, RTT, fairness, stalls, and drops; filter by `purpose` |
   | `metrics/comparisons.csv` | Matched comparisons against CUBIC |
   | `metrics/flows.csv` | Per-flow scores |
   | `metrics/epochs.csv` | Per-epoch scores |
   | `metrics/handovers.csv` | Recovery times and censored events |
   | `<run>/logs_ns3/` | Raw simulation logs |

   Results are saved directly to the mounted host directory; no export is needed.
   For another run, use `python3 code/run_experiment.py results/repeat`.
   Existing output directories are never overwritten.
