# /// script
# requires-python = ">=3.10"
# dependencies = ["matplotlib==3.10.7"]
# ///
"""Plot capacity limits, forward-route hops, injected loss, cwnd, and ACK goodput.

Inside the course container: uv run results/plot_run.py results/baseline
On the host, --routes can supply the run's dynamic-state directory if its
container-only path is unavailable. Missing diagnostics are labeled, not invented.
Hop count includes GSLs and ISLs in the instantaneous forwarding route, not the
measured path of each packet. Capacity/loss diagnostics cover the whole run;
TCP panels and the forward route use --flow. No simulator changes are needed.
"""
import argparse
from bisect import bisect_right
import csv
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator


def csv_rows(path, headers=False):
    with path.open() as stream:
        yield from (csv.DictReader(stream) if headers else csv.reader(stream))


def trace(logs, flow, metric):
    rows = list(csv_rows(logs / f"tcp_flow_{flow}_{metric}.csv"))
    if not rows:
        raise ValueError(f"Empty {metric} trace")
    return [int(r[1]) / 1e9 for r in rows], [int(r[2]) for r in rows]


def hop_counts(routes, config, src, dst, duration):
    forwarding, times, hops = {}, [], []
    step = int(config["dynamic_state_update_interval_ns"])
    if step <= 0:
        raise ValueError("Routing update interval must be positive")
    # fstate files contain incremental updates; an empty file retains all routes.
    ticks = [0] if config.get("satellite_network_force_static") == "true" else range(0, round(duration * 1e9), step)
    for tick in ticks:
        for row in csv_rows(routes / f"fstate_{tick}.txt"):
            if int(row[1]) == dst:
                forwarding[int(row[0])] = int(row[2])
        node, visited, count = src, set(), 0
        while node != dst:
            if node in visited or node < 0 or node not in forwarding:
                count = math.nan  # No route or a forwarding loop, not zero hops.
                break
            visited.add(node)
            node = forwarding[node]
            count += 1
        times.append(tick / 1e9)
        hops.append(count)
    return times + [duration], hops + [hops[-1]]


def unavailable(ax, text):
    ax.text(0.5, 0.5, text, transform=ax.transAxes, ha="center", va="center")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path, help="run directory or logs_ns3 directory")
    parser.add_argument("--flow", type=int, default=0)
    parser.add_argument("--bin-seconds", type=float, default=0.5, help="goodput bin width")
    parser.add_argument("--routes", type=Path, help="override dynamic-state directory")
    parser.add_argument("--output", type=Path, help="default: <run>/overview.png")
    args = parser.parse_args()
    if not math.isfinite(args.bin_seconds) or args.bin_seconds <= 0:
        parser.error("--bin-seconds must be finite and positive")
    run = args.run.resolve()
    if run.name == "logs_ns3":
        run = run.parent
    logs = run / "logs_ns3"
    config = {}
    for line in (run / "config_ns3.properties").read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            config[key.strip()] = value.strip().strip('"')
    duration = int(config["simulation_end_time_ns"]) / 1e9
    if duration <= 0:
        parser.error("Simulation duration must be positive")
    cwnd_t, cwnd = trace(logs, args.flow, "cwnd")
    progress_t, progress = trace(logs, args.flow, "progress")
    fig, axes = plt.subplots(5, 1, figsize=(11, 12), sharex=True)

    queues = logs / "scenario_queues.csv"
    if queues.exists():
        links = {}
        for row in csv_rows(queues, headers=True):
            times, rates = links.setdefault((row["node"], row["device"]), ([], []))
            times.append(int(row["time_ns"]) / 1e9)
            rates.append(int(row["effective_bps"]) / 1e6)
        for (node, device), (times, rates) in links.items():
            axes[0].step(times + [duration], rates + [rates[-1]], where="post",
                         label=f"node {node}, interface {device}")
        if links:
            axes[0].legend(fontsize=7, ncol=2)
        else:
            unavailable(axes[0], "No capacity samples")
    else:
        unavailable(axes[0], "No capacity-limit diagnostics")
    axes[0].set_title("Simulated congestion: capacity on targeted interfaces", fontsize=10)
    axes[0].set_ylabel("Capacity (Mbps)")

    routes = args.routes or run / config["satellite_network_routes_dir"]
    if routes.is_dir():
        schedule = run / config.get("tcp_flow_schedule_filename", "schedule.csv")
        flow = next((r for r in csv_rows(schedule) if int(r[0]) == args.flow), None)
        if flow is None:
            parser.error(f"Flow {args.flow} not found in {schedule}")
        times, hops = hop_counts(routes, config, int(flow[1]), int(flow[2]), duration)
        axes[1].step(times, hops, where="post", color="tab:purple")
    else:
        unavailable(axes[1], "Routing state unavailable: run inside Docker or supply --routes")
        print(f"Warning: cannot read routing state at {routes}")
    axes[1].set_ylabel("Forward route\n(link hops)")
    axes[1].yaxis.set_major_locator(MaxNLocator(integer=True))

    loss_file = logs / "scenario_link_loss.csv"
    if loss_file.exists():
        intervals = {}
        for row in csv_rows(loss_file, headers=True):
            key = (int(row["start_ns"]) / 1e9, int(row["end_ns"]) / 1e9)
            totals = intervals.setdefault(key, [0, 0, 0.0])
            for i, value in enumerate((int(row["attempts"]), int(row["drops"]), float(row["expected_drops"]))):
                totals[i] += value
        times, observed, expected, last_end = [], [], [], 0
        for (start, end), (attempts, drops, errors) in sorted(intervals.items()):
            if start > last_end:
                times.append(last_end)
                observed.append(math.nan)
                expected.append(math.nan)
            times.append(start)
            observed.append(100 * drops / attempts if attempts else math.nan)
            expected.append(100 * errors / attempts if attempts else math.nan)
            last_end = end
        times.append(last_end)
        axes[2].step(times, observed + [math.nan], where="post", color="tab:red", alpha=0.65, label="Injected drops / link attempts")
        axes[2].step(times, expected + [math.nan], where="post", color="black", linestyle="--", linewidth=1, label="Configured probability (traffic-weighted)")
        axes[2].legend(fontsize=7)
        if not intervals:
            unavailable(axes[2], "No link attempts logged")
    else:
        unavailable(axes[2], "No injected-loss diagnostics")
    axes[2].set_ylabel("Link loss (%)")

    axes[3].step(cwnd_t + [duration], [v / 1024 for v in cwnd] + [cwnd[-1] / 1024], where="post")
    axes[3].set_ylabel("cwnd (KiB)")
    edges = [min(i * args.bin_seconds, duration) for i in range(math.ceil(duration / args.bin_seconds) + 1)]
    # Cumulative ACK counts are step functions; do not interpolate across idle time.
    totals = [progress[i] if (i := bisect_right(progress_t, t) - 1) >= 0 else 0 for t in edges]
    rates = [(b - a) * 8 / (right - left) / 1e6 for a, b, left, right in zip(totals, totals[1:], edges, edges[1:])]
    axes[4].stairs(rates, edges, baseline=None)
    axes[4].set_ylabel("ACK goodput (Mbps)")
    axes[4].set_xlabel("Simulation time (s)")
    axes[4].set_xlim(0, duration)
    for ax in axes:
        ax.grid(alpha=0.25)
        ax.set_ylim(bottom=0)
    fig.suptitle(f"{run.name} — flow {args.flow}")
    fig.text(0.5, 0.01, "Capacity/loss panels are run-wide. Capacity reduction does not guarantee queue overflow. No attempts = gaps in loss.",
             ha="center", fontsize=8)
    fig.tight_layout(rect=(0, 0.025, 1, 1))
    output = args.output or run / "overview.png"
    fig.savefig(output, dpi=160)
    plt.close(fig)
    print(f"Saved {output}")


if __name__ == "__main__":
    main()
