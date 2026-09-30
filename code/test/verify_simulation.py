"""Integration checks on the course constellation; run inside the lab container.

Usage from the submission root:
    python3 code/test/verify_simulation.py results/verification

Requires the scenario patch to be applied and built. Uses a new output directory
and seven short simulations (about one minute). Checks zero-loss TCP equivalence,
seed replay, baseline error frequency, forced peak exposure, GSL cap persistence,
overlap/restoration, time-zero ISL caps, queue buildup, and invalid targets.
Test inputs and logs remain in the output directory for inspection.
"""
import argparse
import csv
import math
import os
from pathlib import Path
import resource
import subprocess
import sys

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE))
from gen_loss_state import generate


def rows(path):
    with path.open() as stream:
        return list(csv.DictReader(stream))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    def run(name, *options, success=True):
        target = output / name
        subprocess.run([sys.executable, str(CODE / "prepare_run.py"), str(target), "--duration", "5", *map(str, options)], check=True)
        with (target / "driver.txt").open("w") as stream:
            result = subprocess.run([str(Path(os.environ["LAB"]) / "scripts/run_sim.sh"), str(target)], stdout=stream, stderr=subprocess.STDOUT)
        assert (result.returncode == 0) == success, f"{name}: inspect {target / 'driver.txt'}"
        if success:
            assert (target / "logs_ns3/finished.txt").read_text().strip() == "Yes"
        return target / "logs_ns3"

    def loss(name, base, peak, events=None, types="isl,gsl"):
        config = output / f"{name}.properties"
        config.write_text(f"event_seed=42\nsatellites=all\nlink_types={types}\n"
                          f"base_loss_probability={base}\npeak_loss_probability={peak}\n"
                          "peak_probability_per_second=0\npeak_duration_min_ms=20\npeak_duration_max_ms=20\n")
        directory = output / name
        generate(os.environ["STATE_EXAMPLE"], config, 5, directory)
        if events is not None:
            (directory / "loss_events.csv").write_text(events)
        return directory

    baseline = run("baseline")
    zero = run("zero", "--loss-state", loss("zero_state", 0, 0))
    for metric in ("progress", "rtt", "cwnd"):
        file = f"tcp_flow_0_{metric}.csv"
        assert (baseline / file).read_bytes() == (zero / file).read_bytes(), f"zero loss changed {metric}"
    assert not rows(zero / "scenario_link_drops.csv")
    print("PASS: zero-loss model preserves all TCP traces", flush=True)

    stochastic = loss("random_state", 0.01, 0.01, types="gsl")
    first = run("random_a", "--loss-state", stochastic)
    second = run("random_b", "--loss-state", stochastic)
    for file in ("scenario_link_drops.csv", "scenario_link_loss.csv", "tcp_flow_0_progress.csv"):
        assert (first / file).read_bytes() == (second / file).read_bytes(), f"seed replay changed {file}"
    counts = rows(first / "scenario_link_loss.csv")
    attempts = sum(int(r["attempts"]) for r in counts)
    drops = sum(int(r["drops"]) for r in counts)
    expected = sum(float(r["expected_drops"]) for r in counts)
    assert attempts > 1000 and abs(drops - expected) < 6 * math.sqrt(expected) + 1
    assert drops == len(rows(first / "scenario_link_drops.csv"))
    print(f"PASS: seeded replay and base-loss rate ({drops}/{attempts}; expected {expected:.1f})", flush=True)

    shape = Path(os.environ["STATE_EXAMPLE"], "tles.txt").read_text().splitlines()[0].split()
    n = int(shape[0]) * int(shape[1])
    # At 2 s this baseline TCP is idle in recovery. Exercise a peak while packets are in flight.
    events = "".join(f"{sat},4000000000,4200000000\n" for sat in range(n))
    peaked = run("peak", "--loss-state", loss("peak_state", 0, 1, events))
    drops = rows(peaked / "scenario_link_drops.csv")
    assert drops and all(r["peak"] == "1" and r["probability"] == "1" for r in drops)
    assert all(4e9 <= int(r["time_ns"]) < 4.3e9 for r in drops)
    attempts = sum(int(r["peak_attempts"]) for r in rows(peaked / "scenario_link_loss.csv"))
    assert attempts == len(drops)
    print("PASS: 100% peak drops exactly the packets exposed to the event", flush=True)

    capacity = output / "capacity.txt"
    capacity.write_text("1584,gsl,*,2,3,5\n1584,gsl,*,2.5,3.5,2\n0,isl,*,0,0.5,10\n")
    congested = run("capacity", "--congestion", capacity)
    changes = rows(congested / "scenario_capacity.csv")
    gsl = [r for r in changes if r["node"] == "1584"]
    assert [(int(r["time_ns"]), int(r["effective_bps"])) for r in gsl] == [
        (2000000000, 5000000), (2500000000, 2000000), (3000000000, 2000000), (3500000000, 100000000)]
    isl = [r for r in changes if r["node"] == "0"]
    assert len(isl) == 8 and len({r["device"] for r in isl}) == 4
    initial = [r for r in rows(congested / "scenario_queues.csv") if r["node"] == "0" and r["time_ns"] == "0"]
    assert len(initial) == 4 and all(int(r["effective_bps"]) == 10000000 for r in initial)
    q = [r for r in rows(congested / "scenario_queues.csv") if r["node"] == "1584"]
    for r in q:
        t = int(r["time_ns"]) / 1e9
        expected = 100000000 if t < 2 or t >= 3.5 else 5000000 if t < 2.5 else 2000000
        assert int(r["effective_bps"]) == expected
    assert max(int(r["packets"]) for r in q) == 100
    print("PASS: GSL cap persistence, overlaps, restoration, queue growth, four ISL targets", flush=True)

    invalid = output / "invalid.txt"
    invalid.write_text("1584,isl,*,1,2,10\n")
    run("invalid", "--congestion", invalid, success=False)
    print("PASS: invalid interface selection rejected", flush=True)


if __name__ == "__main__":
    main()
