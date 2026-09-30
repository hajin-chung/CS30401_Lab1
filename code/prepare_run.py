"""Prepare one experiment with the course make_run.sh; does not run ns-3.

Creates config_ns3.properties and schedule.csv for one 100 GB bulk flow. Defaults:
supplied Seoul-London state, CUBIC, 30 s, 100 Mbps links, 100-packet queues, source
1584, destination 1585, seed 123456789. Only BBR enables pacing. Use --help for CLI.

Optional scenario inputs are copied into the run, so later edits to the originals
do not change it. Loss state must match the TLE hash and cover the run duration.
Existing runs are never overwritten. Added config keys are
link_congestion_scenario_file, link_loss_state_dir, link_scenario_log_interval_ms.
Missing scenario keys disable that model; relative paths resolve against the run.

After run_sim.sh, logs_ns3 contains TCP progress (cumulative acknowledged bytes),
smoothed RTT, and congestion-window CSVs. ACK goodput is the progress difference
divided by elapsed time, not transmitted bytes. Added CSVs have unit headers:
  scenario_capacity.csv: cap transitions and effective rates.
  scenario_queues.csv: occupancy/rates on capacity-targeted interfaces.
  scenario_queue_drops.csv: queue-drop callbacks on those interfaces only.
  scenario_link_loss.csv: per-directed-link attempts, errors, peak exposure, and
    expected errors per interval (not end-to-end application loss).
  scenario_link_drops.csv: individual injected errors and probability/peak state.
finished.txt=Yes means simulation completion; the large bulk flow normally stays
NO_ONGOING. Simulator console output is retained in out.txt.
"""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
from gen_loss_state import properties


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--state", default=os.environ.get("STATE_EXAMPLE"), required=not os.environ.get("STATE_EXAMPLE"))
    parser.add_argument("--cc", default="TcpCubic")
    parser.add_argument("--duration", type=int, default=30)
    parser.add_argument("--rate-mbps", type=float, default=100)
    parser.add_argument("--queue-pkts", type=int, default=100)
    parser.add_argument("--src", type=int, default=1584)
    parser.add_argument("--dst", type=int, default=1585)
    parser.add_argument("--seed", type=int, default=123456789)
    parser.add_argument("--congestion", type=Path)
    parser.add_argument("--loss-state", type=Path)
    parser.add_argument("--log-interval-ms", type=float, default=10)
    args = parser.parse_args()
    if args.run_dir.exists():
        parser.error("run directory already exists; use a new directory to preserve prior results")
    if not 0 < args.seed < 2**32 or args.duration <= 0 or args.rate_mbps <= 0 or args.queue_pkts <= 0:
        parser.error("invalid seed, duration, link rate, or queue size")
    for path in (args.congestion, args.loss_state):
        if path is not None and not path.exists():
            parser.error(f"input does not exist: {path}")
    if args.loss_state:
        metadata = properties(args.loss_state / "loss_model.properties")
        actual_hash = hashlib.sha256(Path(args.state, "tles.txt").read_bytes()).hexdigest()
        if metadata["tles_sha256"] != actual_hash or int(metadata["duration_ns"]) < args.duration * 10**9:
            parser.error("loss state has a different constellation or insufficient duration")
    helper = Path(os.environ["LAB"]) / "scripts/make_run.sh"
    env = dict(os.environ, FLOWS="", PACING="true" if args.cc == "TcpBbr" else "false")
    subprocess.run([str(helper), str(args.run_dir.resolve()), str(Path(args.state).resolve()),
                    args.cc, str(args.rate_mbps), str(args.queue_pkts), str(args.duration),
                    str(args.src), str(args.dst)], env=env, check=True)
    config = args.run_dir / "config_ns3.properties"
    text = config.read_text().replace("simulation_seed=123456789", f"simulation_seed={args.seed}")
    if args.congestion:
        shutil.copyfile(args.congestion, args.run_dir / "congestion_scenario.txt")
        text += 'link_congestion_scenario_file="congestion_scenario.txt"\n'
    if args.loss_state:
        shutil.copytree(args.loss_state, args.run_dir / "loss_state")
        text += 'link_loss_state_dir="loss_state"\n'
    if args.congestion or args.loss_state:
        text += f"link_scenario_log_interval_ms={args.log_interval_ms}\n"
    config.write_text(text)


if __name__ == "__main__":
    main()
