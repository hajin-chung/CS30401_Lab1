"""Generate matched scenarios, run ns-3, plot each run, and export scores."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import itertools
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from scenarios.generate_congestion import generate
from scenarios.generate_handover_loss import detect_handovers, load_state, write_scenarios

CODE = Path(__file__).resolve().parent
EXPERIMENT = dict(
    duration=200,
    src=1584, dst=1585,
    cc=['TcpCubic', 'TcpCubicNew', 'TcpBbr'],
    profiles=['constant', 'drop', 'rise', 'alternating', 'random'],
    loss_ms=[0, 30, 100, 200],
    loss_timing='after', handover_kind='all',
    flow_counts=[1, 4], seeds=[123456789],
    rate_mbps=100, low_mbps=20, high_mbps=100,
    queue_pkts=100, log_interval_ms=10,
    jobs=2,
)


def call(script, *args, **kwargs):
    subprocess.run([sys.executable, str(CODE / script), *map(str, args)], check=True, **kwargs)


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_with_progress(command, label, **kwargs):
    started = time.monotonic()
    print(f'{label}', flush=True)
    with subprocess.Popen(command, **kwargs) as process:
        while True:
            try:
                returncode = process.wait(timeout=30)
                break
            except subprocess.TimeoutExpired:
                print(f'{label}: still running ({time.monotonic() - started:.0f}s elapsed)', flush=True)
        if returncode:
            raise subprocess.CalledProcessError(returncode, command)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('output', nargs='?', type=Path, default=CODE.parent / 'results/experiments',
                   help='new output directory (default: results/experiments)')
    p.add_argument('--jobs', type=int, default=EXPERIMENT['jobs'],
                   help='parallel experiments (default: 2)')
    args = p.parse_args()
    if args.jobs < 1:
        p.error('--jobs must be a positive integer')
    if args.output.exists():
        p.error('Output exists; pass a new directory, e.g. python3 code/run_experiment.py results/repeat')
    if not all(os.environ.get(key) for key in ('LAB', 'NS3_DIR', 'STATE_EXAMPLE')):
        p.error('Run inside the course container; see README.md')
    args = argparse.Namespace(**(EXPERIMENT | {'jobs': args.jobs}),
                              output=args.output, state=Path(os.environ['STATE_EXAMPLE']))
    ns3 = Path(os.environ['NS3_DIR']).resolve()
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        p.error('Matplotlib is missing; use the course container from README.md')
    try:
        state = args.state.resolve()
        routes, interval, duration, sats, count = load_state(state, None, args.duration * 10**9)
        if args.src == args.dst or not all(sats <= n < count for n in (args.src, args.dst)):
            raise ValueError('Source and destination must be distinct ground stations')
        events = detect_handovers(routes, interval, duration, args.src, args.dst, count)
    except (ValueError, OSError) as error:
        p.error(str(error))
    output = args.output.resolve()
    output.mkdir(parents=True)
    scenarios = output / 'scenarios'
    write_scenarios(scenarios / 'loss', events, duration, args.loss_timing, args.loss_ms)
    handovers = scenarios / 'loss/handovers.csv'
    source_files = sorted(CODE.glob('*.py')) + sorted((CODE / 'scenarios').glob('*.py')) + [CODE / 'patch/ns3.patch']
    # Hash the routing inputs once, so comparisons identify the actual state used.
    state_digest = hashlib.sha256()
    for path in [state / 'tles.txt', state / 'ground_stations.txt', *[routes / f'fstate_{t}.txt' for t in range(0, duration, interval)]]:
        state_digest.update(path.name.encode())
        state_digest.update(path.read_bytes())
    settings = {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()}
    manifest = dict(settings=settings, state_sha256=state_digest.hexdigest(),
                    source_sha256={str(path.relative_to(CODE)): fingerprint(path) for path in source_files},
                    handovers=len(events), python=sys.version)
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    if not events:
        print('Warning: no handovers in this duration; capacity transitions and outages will not occur.', flush=True)
    runs = []
    for seed, profile in itertools.product(args.seeds, args.profiles):
        scenario = scenarios / f'{profile}-s{seed}'
        generate(scenario, handovers, duration, args.src, profile, args.low_mbps, args.high_mbps, seed)
        for loss_ms, flows, cc in itertools.product(args.loss_ms, args.flow_counts, args.cc):
            name = f'{cc}-{profile}-loss{loss_ms}-n{flows}-s{seed}'
            run = output / name
            call('prepare_run.py', run, '--state', state, '--duration', args.duration, '--cc', cc,
                 '--src', args.src, '--dst', args.dst, '--flows', flows, '--seed', seed,
                 '--rate-mbps', args.rate_mbps, '--queue-pkts', args.queue_pkts,
                 '--log-interval-ms', args.log_interval_ms,
                 '--congestion', scenario / 'congestion_scenario.txt',
                 '--loss', scenarios / f'loss/loss_{loss_ms}ms.txt', stdout=subprocess.DEVNULL)
            for path in (handovers, scenario / 'epochs.csv'):
                shutil.copyfile(path, run / path.name)
            identity = dict(profile=profile, loss_ms=loss_ms, flows=flows, seed=seed,
                            duration=args.duration, src=args.src, dst=args.dst, rate_mbps=args.rate_mbps,
                            queue_pkts=args.queue_pkts, loss_timing=args.loss_timing,
                            handover_kind=args.handover_kind, state_sha256=manifest['state_sha256'],
                            congestion_sha256=fingerprint(run / 'congestion_scenario.txt'),
                            loss_sha256=fingerprint(run / 'loss_scenario.txt'))
            identity['case_id'] = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:20]
            (run / 'experiment.json').write_text(json.dumps(identity | {'cc': cc}, indent=2) + '\n')
            runs.append(run)
    print(f'Prepared {len(runs)} runs; {len(events)} handovers. Output: {output}', flush=True)
    subprocess.run([str(ns3 / 'ns3'), 'build'], cwd=ns3, check=True)
    print(f'Running {len(runs)} experiments, {args.jobs} at a time. Logs: {output}/<run>/out.txt', flush=True)

    def simulate(run):
        started = time.monotonic()
        with (run / 'out.txt').open('w') as stream:
            run_with_progress([str(ns3 / 'ns3'), 'run', f'main_satnet --run_dir={run}', '--no-build'],
                              f'Simulating {run.name}', cwd=ns3, stdout=stream, stderr=subprocess.STDOUT)
        finished = run / 'logs_ns3/finished.txt'
        if not finished.exists() or finished.read_text().strip() != 'Yes':
            raise RuntimeError(f'Simulation did not finish: {run}')
        with (run / 'analysis.log').open('w') as stream:
            run_with_progress([sys.executable, str(CODE / 'plot_run.py'), str(run)],
                              f'Plotting {run.name}', stdout=stream, stderr=subprocess.STDOUT)
        return time.monotonic() - started

    failures = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(simulate, run): run for run in runs}
        for i, future in enumerate(as_completed(futures), 1):
            run = futures[future]
            try:
                elapsed = future.result()
                print(f'[{i}/{len(runs)}] Finished {run.name} ({elapsed:.0f}s)', flush=True)
            except Exception as error:
                failures.append(dict(run=str(run), error=str(error)))
                print(f'[{i}/{len(runs)}] Failed {run.name}; see out.txt / analysis.log', file=sys.stderr, flush=True)
    if failures:
        (output / 'failures.json').write_text(json.dumps(failures, indent=2) + '\n')
        sys.exit(1)
    run_with_progress([sys.executable, str(CODE / 'extract_metrics.py'), str(output),
                       '--output', str(output / 'metrics')], 'Exporting metrics and comparisons')


if __name__ == '__main__':
    main()
