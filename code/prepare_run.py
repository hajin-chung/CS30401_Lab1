"""Prepare a course-container run; scenario files are copied into its directory.

Defaults: CUBIC, 30 s, 100 Mbps, 100-packet queues, Seoul (1584) to London (1585).
Use --cc TcpCubicNew for ECN2 handover control. Only BBR enables pacing.
Run with run_sim.sh afterward; existing output directories are rejected.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--state", default=os.environ.get("STATE_EXAMPLE"), required=not os.environ.get("STATE_EXAMPLE"))
    parser.add_argument("--cc", default="TcpCubic", help="TCP algorithm, e.g. TcpCubicNew, TcpCubic, TcpNewReno, TcpBbr, TcpVegas")
    parser.add_argument("--duration", type=int, default=30)
    parser.add_argument("--rate-mbps", type=float, default=100)
    parser.add_argument("--queue-pkts", type=int, default=100)
    parser.add_argument("--src", type=int, default=1584)
    parser.add_argument("--dst", type=int, default=1585)
    parser.add_argument("--seed", type=int, default=123456789)
    parser.add_argument("--congestion", type=Path)
    parser.add_argument("--loss", type=Path, help="base loss rate and node_id,link_type,peer_id,start_s,end_s,loss_rate rows")
    parser.add_argument("--log-interval-ms", type=float, default=10)
    args = parser.parse_args()
    if args.run_dir.exists():
        parser.error("run directory already exists; use a new directory to preserve prior results")
    if not 0 < args.seed < 2**32 or args.duration <= 0 or args.rate_mbps <= 0 or args.queue_pkts <= 0:
        parser.error("invalid seed, duration, link rate, or queue size")
    for path in (args.congestion, args.loss):
        if path is not None and not path.exists():
            parser.error(f"input does not exist: {path}")
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
    if args.loss:
        shutil.copyfile(args.loss, args.run_dir / "loss_scenario.txt")
        text += 'link_loss_scenario_file="loss_scenario.txt"\n'
    if args.congestion or args.loss:
        text += f"link_scenario_log_interval_ms={args.log_interval_ms}\n"
    config.write_text(text)


if __name__ == "__main__":
    main()
