# CS30401 Lab 1

## 1. Enter the course container

For your existing development container, run on the host:

```bash
docker exec -it lab1 bash
cd /work/CS30401_Lab1
```

For a fresh reproduction instead, run from the directory containing this README
on the host. Use unused container and volume names:

```bash
docker run -d --rm --name lab1-clean \
  -v "$PWD":/work \
  ghcr.io/yeonjong21/cs30401-lab1:2026f-v1 sleep infinity
docker exec -it lab1-clean bash
cd /work
```

Run all commands below inside the container, from the directory containing
this README. No additional Python packages or constellation generation needed.

## 2. Apply changes and build

```bash
bash code/apply_patch.sh
```

Allow a few minutes for the first build. This also works if the patch is already
applied.

## 3. Run experiments

Use new output directory names each time. Each experiment below simulates 30 s;
allow roughly 10–60 s per run on this host, depending on the scenario.

Baseline:

```bash
python3 code/prepare_run.py results/baseline --duration 30
run_sim.sh "$PWD/results/baseline"
```

Congestion only (edit `code/scenarios/congestion_scenario.txt` first):

```bash
python3 code/prepare_run.py results/congestion --duration 30 \
  --congestion code/scenarios/congestion_scenario.txt
run_sim.sh "$PWD/results/congestion"
```

Packet loss only (edit `code/scenarios/loss_model.properties` first):

```bash
python3 code/gen_loss_state.py "$STATE_EXAMPLE" \
  --config code/scenarios/loss_model.properties --duration 30 \
  --output results/loss_state
python3 code/prepare_run.py results/loss --duration 30 \
  --loss-state results/loss_state
run_sim.sh "$PWD/results/loss"
```

Both, reusing the loss state generated above:

```bash
python3 code/prepare_run.py results/combined --duration 30 \
  --congestion code/scenarios/congestion_scenario.txt \
  --loss-state results/loss_state
run_sim.sh "$PWD/results/combined"
```

Add `--cc TcpBbr` or `--cc TcpVegas` to a preparation command to change TCP
(default: `TcpCubic`). Add `--rate-mbps 100 --queue-pkts 20 --seed 123456789`
to set link rate, queue size, and simulation seed. For all options:

```bash
python3 code/prepare_run.py --help
python3 code/gen_loss_state.py --help
```

Results are under `results/<run>/logs_ns3/`; console output is in
`results/<run>/out.txt`.

## 4. Run checks (optional)

```bash
python3 -m unittest discover -s code -p 'test_*.py' -v
python3 code/test/verify_simulation.py results/verification
```

Allow about one minute for the integration checks; use a new output directory.

## 5. After making further ns-3 changes

From the directory containing this README:

```bash
submission_dir="$PWD"
cd "$NS3_DIR"
./ns3 build
make_patch.sh "$submission_dir/code/ns3.patch"
cd "$submission_dir"
```
