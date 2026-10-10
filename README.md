# CS30401 Lab 1

## Setup

Use the course **ns-3.48 + LEO modules** image; upstream ns-3.48 alone lacks the
required modules. From this directory on the host, choose an unused container name:

```bash
docker run -d --rm --name lab1-ecn2 -v "$PWD":/work \
  ghcr.io/yeonjong21/cs30401-lab1:2026f-v1 sleep infinity
docker exec -it lab1-ecn2 bash
cd /work
bash code/apply_patch.sh
```

Run the remaining commands inside the container, from this directory.
The helper applies `code/ns3.patch` to the course baseline (`cs30401-base`,
`99bc6f7`), or `code/ecn2-upgrade.patch` to our previous version (`466fd6e`).
Repeated application is safe; conflicting local edits require a fresh tree or
manual resolution. Allow a few minutes to build.

The merged patch contains ECN2's `TcpCubicNew` and scheduled congestion/loss.
TcpRA's contribution is now part of TcpCubicNew; the separate TcpRA class is removed.
ISL path changes toggle ECN/ECE, triggering slow-start restart and retransmission.
ECN is used for handover signaling here, not standard congestion feedback.

## Experiments

Run all **20 experiments** (NewReno, CUBIC, CubicNew, BBR, Vegas × baseline,
congestion, loss, combined):

```bash
bash results/experiments.sh results/ecn2-matrix
```

Each runs for 200 s at 100 Mbps with 100-packet queues and seed 123456789.
The matrix uses `congestion_scenario2.txt` (40–45 s) and `loss_scenario.txt`;
combined runs apply both. It verifies CubicNew traces and creates `overview.png`
using `uv` (the first plot may download Matplotlib). Always use new output paths.

For one 30 s run:

```bash
python3 code/prepare_run.py results/combined --cc TcpCubicNew --duration 30 \
  --congestion code/scenarios/congestion_scenario.txt \
  --loss code/scenarios/loss_scenario.txt
run_sim.sh "$PWD/results/combined"
uv run results/plot_run.py results/combined
```

Omit either scenario option for individual effects, or both for baseline.
`--cc` defaults to `TcpCubic`; see `python3 code/prepare_run.py --help` for options.
Results are in `logs_ns3/`, console output in `out.txt`. `finished.txt=Yes` means
success; the 100 GB flow normally remains ongoing. CubicNew also logs
`isl-route-ecn.csv` and `tcp-ece.csv`.

## Checks

```bash
submission_dir="$PWD"
cd "$NS3_DIR"
./ns3 configure --enable-tests --enable-examples
./ns3 build
./test.py --no-build -s tcp-ecn-test
./test.py --no-build -s tcp-dctcp-test
cd "$submission_dir"
python3 "$NS3_DIR/utils/verify-cubic-new-ecn.py" \
  --regression "$PWD/results/ecn2-checks" --state "$STATE_EXAMPLE"
python3 code/test/verify_simulation.py results/scenario-checks
```

Use new check directories. To verify a completed CubicNew run, pass its directory
to `verify-cubic-new-ecn.py` without `--regression`.

## Export further changes

```bash
make_patch.sh "$PWD/code/ns3.patch"
git diff -- code/ns3.patch
```

The exporter stages all ns-3 changes against `cs30401-base`. Regenerate
`ecn2-upgrade.patch` against submission `466fd6e` too, and test both install paths.
