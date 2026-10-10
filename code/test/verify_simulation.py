"""Check scheduled loss, capacity caps, seeded replay, and invalid inputs.

Run inside the patched container with a new output directory:
    python3 code/test/verify_simulation.py results/verification
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

    baseline = run("baseline")
    def schedule(name, contents):
        path = output / f"{name}.txt"
        path.write_text(contents)
        return path

    scheduled_zero = run("scheduled_zero", "--loss", schedule("no_loss", "base_loss_rate=0\n1584,gsl,*,0,5,0\n"))
    for metric in ("progress", "rtt", "cwnd"):
        file = f"tcp_flow_0_{metric}.csv"
        assert (baseline / file).read_bytes() == (scheduled_zero / file).read_bytes()
    assert not rows(scheduled_zero / "scenario_link_drops.csv")

    # Unordered/adjacent windows and simultaneous windows on different interfaces.
    # Only the two ground stations receive base loss; ISLs remain unaffected.
    rates = schedule("rates", "0.00001\n1584,gsl,*,2,4,0.01\n1584,gsl,*,0,1,0.001\n"
                     "1584,gsl,*,1,2,0\n1585,gsl,*,0,4,0\n")
    scheduled = run("scheduled_a", "--loss", rates)
    replay = run("scheduled_b", "--loss", rates)
    for file in ("scenario_link_drops.csv", "scenario_link_loss.csv", "tcp_flow_0_progress.csv"):
        assert (scheduled / file).read_bytes() == (replay / file).read_bytes()

    def rate_at(src, t):
        if src == 1584:
            return 0.001 if t < 1e9 else 0 if t < 2e9 else 0.01 if t < 4e9 else 0.00001
        return 0.00001 if src == 1585 and t >= 4e9 else 0

    counts = rows(scheduled / "scenario_link_loss.csv")
    assert counts and any(int(r["start_ns"]) >= 4e9 for r in counts)
    drops = sum(int(r["drops"]) for r in counts)
    expected = sum(float(r["expected_drops"]) for r in counts)
    assert expected > 0 and abs(drops - expected) < 6 * math.sqrt(expected) + 1
    assert drops == len(rows(scheduled / "scenario_link_drops.csv"))
    for r in counts:
        start, end = int(r["start_ns"]), int(r["end_ns"])
        src = int(r["src"])
        if rate_at(src, start) == rate_at(src, end):
            assert math.isclose(float(r["expected_drops"]), int(r["attempts"]) * rate_at(src, start), abs_tol=1e-10)
    for r in rows(scheduled / "scenario_link_drops.csv"):
        assert float(r["probability"]) == rate_at(int(r["src"]), int(r["time_ns"]))

    outage = run("scheduled_outage", "--loss", schedule("outage", "base_loss_rate=0\n1584,gsl,*,4,4.1,1\n"))
    drops = rows(outage / "scenario_link_drops.csv")
    assert drops and all(r["src"] == "1584" and 4e9 <= int(r["time_ns"]) < 4.1e9 and r["probability"] == "1" for r in drops)
    assert sum(int(r["peak_attempts"]) for r in rows(outage / "scenario_link_loss.csv")) == len(drops)
    reverse = run("reverse_loss", "--loss", schedule("reverse", "0\n1585,gsl,*,0,5,0.02\n"))
    drops = rows(reverse / "scenario_link_drops.csv")
    assert drops and all(r["src"] == "1585" and int(r["bytes"]) < 100 for r in drops)

    # Pick a real, busy ISL from the zero-loss run rather than hard-coding a route.
    attempts = {}
    for r in rows(scheduled_zero / "scenario_link_loss.csv"):
        pair = int(r["src"]), int(r["dst"])
        if max(pair) < 1584:
            attempts[pair] = attempts.get(pair, 0) + int(r["attempts"])
    src, dst = max(attempts, key=attempts.get)
    for name, peer in (("isl_peer", str(dst)), ("isl_wildcard", "*")):
        logs = run(name, "--loss", schedule(name, f"0\n{src},isl,{peer},0,5,0.02\n"))
        drops = rows(logs / "scenario_link_drops.csv")
        assert drops and all(int(r["src"]) == src and (peer == "*" or int(r["dst"]) == dst) for r in drops)
        for r in rows(logs / "scenario_link_loss.csv"):
            targeted = int(r["src"]) == src and int(r["dst"]) < 1584 and (peer == "*" or int(r["dst"]) == dst)
            assert math.isclose(float(r["expected_drops"]), int(r["attempts"]) * (0.02 if targeted else 0), abs_tol=1e-10)
    print("PASS: targeted rates, zero-loss equivalence, base restoration, replay, outage, GSL direction, ISL selectors", flush=True)

    for i, contents in enumerate(("", "base_loss_rate=1.1\n", "0\n1584,gsl,*,1,2,1.1\n",
                                  "0\n1584,gsl,*,1,3,0.1\n1584,gsl,*,2,4,0.2\n",
                                  "0\n1584,gsl,*,4,6,1\n", "0\n1584,isl,*,1,2,0.1\n",
                                  "0\n1584,gsl,0,1,2,0.1\n", "0\n0,isl,1584,1,2,0.1\n",
                                  f"0\n{src},isl,*,1,3,0.1\n{src},isl,{dst},2,4,0.2\n",
                                  "0\n1,2,0.1\n")):
        run(f"invalid_loss_{i}", "--loss", schedule(f"invalid_loss_input_{i}", contents), success=False)
    print("PASS: invalid loss schedules rejected", flush=True)

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
