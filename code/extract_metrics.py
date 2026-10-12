"""Export run, flow, epoch, recovery, and paired comparison CSVs. No third-party packages."""
import argparse
import csv
import json
import math
from pathlib import Path
import statistics

from run_data import Run, at, edges, goodput, jain, rows, time_stats, trace


def measure(run, start=0, bin_s=0.5, recovery_fraction=0.9, hold_bins=3):
    if not 0 <= start < run.duration:
        raise ValueError(f'Warmup must be shorter than {run.path.name}')
    progress = {f: trace(run.logs, f, 'progress') for f in run.schedule}
    rtts = {f: trace(run.logs, f, 'rtt') for f in run.schedule}
    bounds = edges(start, run.duration, bin_s)
    rates = {f: goodput(p, bounds) for f, p in progress.items()}
    aggregate = [sum(v) for v in zip(*rates.values())]
    # Equal demands and common starts are required for this simple fairness score.
    comparable = len({(v['src'], v['dst'], v['start'], v['size']) for v in run.schedule.values()}) == 1
    comparable &= all(v['start'] <= start for v in run.schedule.values())
    comparable &= all(at(progress[f], run.duration) < v['size'] for f, v in run.schedule.items())
    totals = {f: (at(p, run.duration) - at(p, start)) for f, p in progress.items()}
    flow_rows = []
    for f, p in progress.items():
        mean, p95 = time_stats(rtts[f], start, run.duration, 1e-6)
        flow_rows.append(dict(flow=f, acked_bytes=totals[f], goodput_mbps=totals[f] * 8e-6 / (run.duration - start),
                              srtt_time_mean_ms=mean, srtt_time_p95_ms=p95))
    stall = longest = current = 0
    for rate, a, b in zip(aggregate, bounds, bounds[1:]):
        if rate == 0:
            stall += b - a
            current += b - a
            longest = max(longest, current)
        else:
            current = 0
    capacity = run.capacity_mbit(start, run.duration)
    fairness = jain(list(totals.values())) if comparable else None
    bin_fairness = [(jain(list(v)), b - a) for v, a, b in zip(zip(*rates.values()), bounds, bounds[1:])]
    valid = [(v, w) for v, w in bin_fairness if v is not None]
    summary = dict(cc=run.config['tcp_socket_type'], seed=int(run.config['simulation_seed']),
                   flows=len(progress), start_s=start, end_s=run.duration, bin_s=bin_s,
                   acked_bytes=sum(totals.values()), goodput_mbps=sum(totals.values()) * 8e-6 / (run.duration - start),
                   capacity_normalized_goodput=sum(totals.values()) * 8e-6 / capacity if capacity else None,
                   jain_goodput=fairness,
                   jain_bin_time_mean=sum(v*w for v, w in valid) / sum(w for _, w in valid) if valid and comparable else None,
                   zero_goodput_fraction=stall / (run.duration - start), longest_zero_goodput_s=longest,
                   srtt_flow_mean_ms=statistics.mean([r['srtt_time_mean_ms'] for r in flow_rows if r['srtt_time_mean_ms'] is not None])
                   if any(r['srtt_time_mean_ms'] is not None for r in flow_rows) else None)
    for filename, key in [('scenario_queue_drops.csv', 'queue_drop_packets'), ('scenario_link_drops.csv', 'injected_drop_packets')]:
        path = run.logs / filename
        summary[key] = sum(start <= int(r['time_ns']) / 1e9 < run.duration for r in rows(path)) if path.exists() else None
    epoch_rows = []
    for epoch in run.epochs:
        a, b = max(start, epoch['start_s']), min(run.duration, epoch['end_s'])
        if b <= a:
            continue
        counts = [at(p, b) - at(p, a) for p in progress.values()]
        mbps = sum(counts) * 8e-6 / (b - a)
        epoch_rows.append(dict(epoch=int(epoch['epoch']), start_s=a, end_s=b, capacity_mbps=epoch['capacity_mbps'],
                               goodput_mbps=mbps, capacity_normalized_goodput=mbps / epoch['capacity_mbps'],
                               jain_goodput=jain(counts) if comparable else None))
    _, losses = run.scenario('link_loss_scenario_file')
    recovery_rows = []
    for i, t in enumerate(run.handovers):
        if t < start or t >= run.duration:
            continue
        deadline = run.handovers[i + 1] if i + 1 < len(run.handovers) else run.duration
        outage_end = max([t] + [b for _, _, _, a, b, p in losses if a <= t < b and p == 1])
        epoch = next((e for e in run.epochs if e['start_s'] <= t < e['end_s']), None)
        target = epoch['capacity_mbps'] * recovery_fraction if epoch else None
        recovered = None
        consecutive = 0
        if target and outage_end < deadline:
            local_bounds = edges(outage_end, deadline, bin_s)
            local_rates = [sum(v) for v in zip(*(goodput(p, local_bounds) for p in progress.values()))]
            for k, (rate, a, b) in enumerate(zip(local_rates, local_bounds, local_bounds[1:])):
                consecutive = consecutive + 1 if rate >= target and b - a >= bin_s - 1e-9 else 0
                if consecutive >= hold_bins:
                    recovered = local_bounds[k + 1 - hold_bins] - outage_end
                    break
        recovery_rows.append(dict(handover_s=t, outage_end_s=outage_end, deadline_s=deadline,
                                  target_mbps=target, recovery_s=recovered,
                                  observed_window_s=max(0, deadline - outage_end),
                                  censored=target is not None and recovered is None))
    recoveries = [r['recovery_s'] for r in recovery_rows if r['recovery_s'] is not None]
    summary.update(handover_count=len(recovery_rows), recovery_observed_count=len(recoveries),
                   recovery_censored_count=sum(r['censored'] for r in recovery_rows),
                   recovery_median_s=statistics.median(recoveries) if recoveries else None)
    return summary, flow_rows, epoch_rows, recovery_rows


def write_csv(path, records):
    with path.open('w', newline='') as stream:
        if records:
            writer = csv.DictWriter(stream, fieldnames=list(dict.fromkeys(k for r in records for k in r)))
            writer.writeheader()
            writer.writerows(records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runs', nargs='+', type=Path, help='individual runs or matrix directories')
    parser.add_argument('--output', type=Path, required=True, help='directory for CSVs')
    parser.add_argument('--warmup', type=float, default=0)
    parser.add_argument('--bin-seconds', type=float, default=0.5)
    parser.add_argument('--recovery-fraction', type=float, default=0.9)
    parser.add_argument('--hold-bins', type=int, default=3)
    parser.add_argument('--baseline', default='TcpCubic')
    args = parser.parse_args()
    if (not all(math.isfinite(v) for v in (args.warmup, args.bin_seconds, args.recovery_fraction))
            or args.warmup < 0 or args.bin_seconds <= 0 or not 0 < args.recovery_fraction <= 1 or args.hold_bins < 1):
        parser.error('Invalid measurement window or recovery options')
    paths = set()
    for path in args.runs:
        if path.name == 'logs_ns3':
            path = path.parent
        paths.update([path.resolve()] if (path / 'config_ns3.properties').exists()
                     else (p.parent.resolve() for p in path.rglob('config_ns3.properties')))
    if not paths:
        parser.error('No runs found')
    summaries, flows, epochs, handovers = [], [], [], []
    try:
        for path in sorted(paths):
            run = Run(path)
            summary, f, e, h = measure(run, args.warmup, args.bin_seconds, args.recovery_fraction, args.hold_bins)
            identity = dict(run=str(path), **{k: run.meta.get(k) for k in ('case_id', 'profile', 'loss_ms', 'loss_timing')})
            summaries.append(identity | summary)
            for target, source in ((flows, f), (epochs, e), (handovers, h)):
                target.extend(identity | row for row in source)
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))
    comparisons = []
    baselines = {}
    for row in summaries:
        if row['cc'] == args.baseline and row['case_id']:
            if row['case_id'] in baselines:
                parser.error('Ambiguous duplicate baseline case; score each matrix separately')
            baselines[row['case_id']] = row
    for row in summaries:
        baseline = baselines.get(row['case_id'])
        if baseline and row['cc'] != args.baseline:
            comparison = dict(run=row['run'], baseline_run=baseline['run'], case_id=row['case_id'], cc=row['cc'])
            for metric in ('goodput_mbps', 'srtt_flow_mean_ms', 'jain_goodput', 'zero_goodput_fraction', 'queue_drop_packets'):
                a, b = row[metric], baseline[metric]
                comparison[metric + '_delta'] = a - b if a is not None and b is not None else None
            comparison['goodput_gain_pct'] = 100 * (row['goodput_mbps'] / baseline['goodput_mbps'] - 1) if baseline['goodput_mbps'] else None
            comparisons.append(comparison)
    args.output.mkdir(parents=True, exist_ok=True)
    for name, records in [('summary', summaries), ('flows', flows), ('epochs', epochs), ('handovers', handovers), ('comparisons', comparisons)]:
        write_csv(args.output / f'{name}.csv', records)
    (args.output / 'measurement.json').write_text(json.dumps(dict(warmup_s=args.warmup, bin_s=args.bin_seconds,
        recovery_fraction=args.recovery_fraction, hold_bins=args.hold_bins, baseline=args.baseline), indent=2) + '\n')
    print(f'Scored {len(paths)} runs: {args.output}')


if __name__ == '__main__':
    main()
