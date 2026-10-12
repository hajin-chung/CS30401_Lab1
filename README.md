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

   This runs the fixed 120-experiment matrix: CUBIC/CubicNew/BBR, five capacity
   profiles, 0/30/100/200 ms outages, and 1/4 flows. Each simulation lasts 200 s.
   Scenarios, plots, and scores are generated automatically.
   By default, two experiments run in parallel. To use eight:

   ```bash
   python3 code/run_experiment.py --jobs 8
   ```

3. Open `results/experiments/` on your host computer:

   | File | Contents |
   | --- | --- |
   | `<run>/overview.png` | Handover, congestion, loss, and TCP plots |
   | `metrics/summary.csv` | Goodput, RTT, fairness, stalls, and drops |
   | `metrics/comparisons.csv` | Matched comparisons against CUBIC |
   | `metrics/flows.csv` | Per-flow scores |
   | `metrics/epochs.csv` | Per-epoch scores |
   | `metrics/handovers.csv` | Recovery times and censored events |
   | `<run>/logs_ns3/` | Raw simulation logs |

   Results are saved directly to the mounted host directory; no export is needed.
   For another run, use `python3 code/run_experiment.py results/repeat`.
   Existing output directories are never overwritten.
