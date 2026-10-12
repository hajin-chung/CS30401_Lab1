# /// script
# requires-python = ">=3.10"
# dependencies = ["matplotlib==3.10.8"]
# ///
"""Plot one run's handovers, capacity, loss, queues, TCP traces, and goodput."""
import argparse
from collections import defaultdict
import math
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from run_data import Run, edges, goodput, jain, rows, trace


def missing(ax, message):
    ax.text(0.5, 0.5, message, transform=ax.transAxes, ha='center', va='center', fontsize=9)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--flow', type=int, help='restrict TCP panels to one flow; default: all')
    parser.add_argument('--bin-seconds', type=float, default=0.5)
    parser.add_argument('--from', dest='start', type=float, default=0)
    parser.add_argument('--to', dest='end', type=float)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        run = Run(args.run)
        end = run.duration if args.end is None else args.end
        if (not all(math.isfinite(v) for v in (args.start, end, args.bin_seconds))
                or not 0 <= args.start < end <= run.duration or args.bin_seconds <= 0):
            raise ValueError('Invalid plot range or bin width')
        flows = [args.flow] if args.flow is not None else list(run.schedule)
        if any(f not in run.schedule for f in flows):
            raise ValueError('Unknown flow ID')
        bounds = edges(args.start, end, args.bin_seconds)
        fig, axes = plt.subplots(8, 1, figsize=(12, 17), sharex=True)
        ax = axes[0]
        _, caps = run.scenario('link_congestion_scenario_file')
        base_rate = float(run.config['gsl_data_rate_megabit_per_s'])
        grouped = defaultdict(list)
        for node, link, peer, a, b, value in caps:
            grouped[node, link, peer].append((a, b, value))
        for key, intervals in grouped.items():
            times = sorted({0, run.duration, *(t for a, b, _ in intervals for t in (a, b))})
            base = float(run.config[f'{key[1]}_data_rate_megabit_per_s'])
            values = [min([base] + [v for a, b, v in intervals if a <= t < b]) for t in times]
            ax.step(times, values, where='post', label=f'{key[0]} {key[1]} → {key[2]}')
        if not caps:
            ax.axhline(base_rate, label='Base GSL rate')
        ax.set_ylabel('Capacity\n(Mbps)')
        ax.legend(fontsize=7, loc='upper right')

        ax = axes[1]
        base, losses = run.scenario('link_loss_scenario_file')
        grouped = defaultdict(list)
        for node, link, peer, a, b, value in losses:
            grouped[node, link, peer].append((a, b, value))
        for key, intervals in grouped.items():
            times = sorted({0, run.duration, *(t for a, b, _ in intervals for t in (a, b))})
            values = [100 * next((v for a, b, v in intervals if a <= t < b), base) for t in times]
            ax.step(times, values, where='post', alpha=0.7, label=f'{key[0]} {key[1]}')
        if not grouped:
            ax.axhline(0)
        else:
            ax.legend(fontsize=7, loc='upper right')
        ax.set_ylabel('Scheduled\nloss (%)')
        ax.set_ylim(-2, 105)

        ax = axes[2]
        queues = run.logs / 'scenario_queues.csv'
        if queues.exists():
            grouped = defaultdict(lambda: ([], []))
            for row in rows(queues):
                t = int(row['time_ns']) / 1e9
                if args.start <= t <= end:
                    ts, vs = grouped[row['node'], row['device']]
                    ts.append(t)
                    vs.append(int(row['packets']))
            for key, (ts, vs) in grouped.items():
                ax.step(ts, vs, where='post', label=f'node {key[0]}, interface {key[1]}')
            if grouped:
                ax.legend(fontsize=7, loc='upper right')
        else:
            missing(ax, 'Queue traces unavailable')
        ax.set_ylabel('Queue\n(packets)')

        ax = axes[3]
        for name, label in [('scenario_queue_drops.csv', 'Queue drops'), ('scenario_link_drops.csv', 'Injected drops')]:
            path = run.logs / name
            if path.exists():
                counts = [0] * (len(bounds) - 1)
                for row in rows(path):
                    t = int(row['time_ns']) / 1e9
                    if args.start <= t < end:
                        k = min(int((t - args.start) / args.bin_seconds), len(counts) - 1)
                        counts[k] += 1
                ax.stairs([n / (b - a) for n, a, b in zip(counts, bounds, bounds[1:])], bounds, baseline=None, label=label)
        if ax.get_legend_handles_labels()[0]:
            ax.legend(fontsize=7, loc='upper right')
        else:
            missing(ax, 'Drop traces unavailable')
        ax.set_ylabel('Run-wide drops\n(packets/s)')

        all_rates = []
        for flow in flows:
            for ax, metric, scale in [(axes[4], 'cwnd', 1 / 1024), (axes[5], 'rtt', 1e-6)]:
                ts, vs = trace(run.logs, flow, metric)
                ax.step(ts + [run.duration], [v * scale for v in vs] + [vs[-1] * scale], where='post', linewidth=0.8, label=f'flow {flow}')
            rates = goodput(trace(run.logs, flow, 'progress'), bounds)
            all_rates.append(rates)
            axes[6].stairs(rates, bounds, baseline=None, linewidth=0.8, label=f'flow {flow}')
        axes[4].set_ylabel('cwnd (KiB)')
        axes[5].set_ylabel('Smoothed RTT\n(ms)')
        axes[6].set_ylabel('ACK goodput\n(Mbps)')
        if len(flows) > 1:
            axes[6].stairs([sum(v) for v in zip(*all_rates)], bounds, baseline=None, color='black', label='aggregate')
        for ax in axes[4:7]:
            ax.legend(fontsize=7, loc='upper right', ncol=min(4, len(flows)))

        ax = axes[7]
        if len(flows) > 1:
            values = [jain(list(v)) for v in zip(*all_rates)]
            ax.stairs([v if v is not None else math.nan for v in values], bounds, baseline=None)
            ax.set_ylim(0, 1.05)
            ax.set_ylabel('Jain index\n(per bin)')
        else:
            path = run.logs / 'tcp-ece.csv'
            events = defaultdict(lambda: ([], []))
            if path.exists():
                for row in rows(path):
                    if row['event'] == 'ack_received' and int(row['node']) == run.schedule[flows[0]]['src']:
                        ts, vs = events[row['node'], row['local_port']]
                        level = int(bool(int(row['flags']) & 64))
                        if not vs or vs[-1] != level:
                            ts.append(int(row['time_ns']) / 1e9)
                            vs.append(level)
            if events:
                for label, (ts, vs) in events.items():
                    ax.step(ts + [run.duration], vs + [vs[-1]], where='post', label=f'node {label[0]}, port {label[1]}')
                ax.legend(fontsize=7, loc='upper right')
                ax.set_yticks([0, 1])
            else:
                missing(ax, 'No ECE feedback trace available')
            ax.set_ylabel('Received ECE\n(source sockets)')
        for ax in axes:
            for t in run.handovers:
                if args.start <= t <= end:
                    ax.axvline(t, color='tab:purple', alpha=0.3, linestyle='--', linewidth=0.6)
            ax.grid(alpha=0.2)
            if ax not in (axes[1], axes[7]):
                ax.set_ylim(bottom=0)
        axes[-1].set_xlim(args.start, end)
        axes[-1].set_xlabel('Simulation time (s); purple dashed lines = scheduled path changes')
        fig.suptitle(f'{run.path.name} · {run.config["tcp_socket_type"]}')
        fig.tight_layout()
        output = args.output or run.path / 'overview.png'
        output.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output, dpi=150)
        plt.close(fig)
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))
    print(f'Saved {output}')


if __name__ == '__main__':
    main()
