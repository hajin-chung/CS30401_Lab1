"""Generate 30/100/200 ms ground-station GSL outages from LEO path changes."""
import argparse
import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import os
from pathlib import Path
import re

NS_PER_SECOND = 1_000_000_000


def seconds(ns):
    return format(Decimal(ns) / NS_PER_SECOND, 'f')


def nanoseconds(value):
    try:
        scaled = Decimal(value) * NS_PER_SECOND
        if not scaled.is_finite() or scaled != scaled.to_integral_value():
            raise ValueError
        return int(scaled)
    except (InvalidOperation, ValueError, OverflowError):
        raise argparse.ArgumentTypeError('Use a finite time with at most nanosecond precision') from None


@dataclass(frozen=True)
class Handover:
    time_ns: int
    before: tuple
    after: tuple


def path_to(table, source, target, node_count):
    path, visited = [], set()
    node = source
    while node != target:
        if node in visited:
            raise ValueError(f'Routing loop from {source} to {target} at node {node}')
        visited.add(node)
        if (node, target) not in table:
            raise ValueError(f'Missing route from node {node} to {target}')
        peer, own_if, peer_if = table[node, target]
        if not 0 <= peer < node_count or min(own_if, peer_if) < 0:
            raise ValueError(f'Unreachable route from node {node} to {target}')
        path.append((node, peer, own_if, peer_if))
        node = peer
    return tuple(path)


def detect_handovers(routes, interval_ns, duration_ns, source, target, node_count):
    table, previous, events = {}, None, []
    for stamp in range(0, duration_ns, interval_ns):
        snapshot = routes / f'fstate_{stamp}.txt'
        with snapshot.open() as stream:
            for row in csv.reader(stream):
                if not row:
                    continue
                try:
                    node, dest, peer, own_if, peer_if = map(int, row)
                except ValueError:
                    raise ValueError(f'Invalid forwarding row in {snapshot}: {row}') from None
                if not 0 <= node < node_count or not 0 <= dest < node_count:
                    raise ValueError(f'Invalid node in {snapshot}: {row}')
                if dest in (source, target):
                    table[node, dest] = peer, own_if, peer_if
        try:
            paths = tuple(path_to(table, src, dst, node_count)
                          for src, dst in ((source, target), (target, source)))
        except ValueError as error:
            raise ValueError(f'{snapshot.name}: {error}') from error
        if previous is not None and previous != paths:
            events.append(Handover(stamp, previous, paths))
        previous = paths
    return events


def merge_windows(windows):
    merged = []
    for start, end in sorted(windows):
        if merged and start <= merged[-1][1]:
            merged[-1] = merged[-1][0], max(merged[-1][1], end)
        else:
            merged.append((start, end))
    return merged


def select_handovers(events, kind):
    if kind == 'all':
        return events
    return [event for event in events if any(
        (old[0], old[-1]) != (new[0], new[-1])
        for old, new in zip(event.before, event.after))]


def loss_windows(events, lead_ns, duration_ns, timing='before'):
    windows = {}
    for event in events:
        if timing == 'after':
            start, end = event.time_ns, min(duration_ns, event.time_ns + lead_ns)
        else:
            start, end = max(0, event.time_ns - lead_ns), min(duration_ns, event.time_ns + 1)
        for path in event.before:
            key = (path[0][0], 'gsl', '*')
            windows.setdefault(key, []).append((start, end))
    return {key: merge_windows(intervals) for key, intervals in windows.items()}


def load_state(state, routes, duration_ns):
    with (state / 'tles.txt').open() as stream:
        num_planes, per_plane = map(int, stream.readline().split())
    if min(num_planes, per_plane) <= 0:
        raise ValueError('Invalid satellite count in tles.txt')
    num_sats = num_planes * per_plane
    with (state / 'ground_stations.txt').open() as stream:
        grounds = [row for row in csv.reader(stream) if row]
    if not grounds or [int(row[0]) for row in grounds] != list(range(len(grounds))):
        raise ValueError('Ground-station IDs must be consecutive starting at zero')
    if routes is None:
        candidates = sorted(p for p in state.glob('dynamic_state_*ms_for_*s') if p.is_dir())
        if len(candidates) != 1:
            raise ValueError('Expected one dynamic-state directory; select one with --routes')
        routes = candidates[0]
    match = re.fullmatch(r'dynamic_state_(\d+)ms_for_(\d+)s', routes.name)
    if not match:
        raise ValueError('Routes directory must be named dynamic_state_<interval>ms_for_<duration>s')
    interval_ns = int(match[1]) * 1_000_000
    available_ns = int(match[2]) * NS_PER_SECOND
    duration_ns = available_ns if duration_ns is None else duration_ns
    if interval_ns <= 0 or not 0 < duration_ns <= available_ns:
        raise ValueError('Duration must be positive and fit the routing state')
    return routes, interval_ns, duration_ns, num_sats, num_sats + len(grounds)


def write_scenarios(output, events, duration_ns, timing='before', windows_ms=(30, 100, 200)):
    output.mkdir(parents=True, exist_ok=False)
    with (output / 'handovers.csv').open('w', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['time_s', 'source', 'target', 'old_path', 'new_path'])
        for event in events:
            for old, new in zip(event.before, event.after):
                if old != new:
                    paths = ['>'.join(map(str, [p[0][0]] + [hop[1] for hop in p])) for p in (old, new)]
                    writer.writerow([seconds(event.time_ns), old[0][0], old[-1][1], *paths])
    for lead_ms in windows_ms:
        windows = loss_windows(events, lead_ms * 1_000_000, duration_ns, timing) if lead_ms else {}
        with (output / f'loss_{lead_ms}ms.txt').open('w') as stream:
            interval = (f'[handover, handover + {lead_ms} ms)' if timing == 'after'
                        else f'[handover - {lead_ms} ms, handover + 1 ns)')
            stream.write(f'# 100% loss on {interval}.\n'
                         '# Both ground stations: outgoing GSLs only. Overlapping windows are merged.\n'
                         '# node_id,link_type,peer_id,start_s,end_s,loss_rate\n'
                         'base_loss_rate=0\n')
            for (node, link, peer), intervals in sorted(windows.items()):
                for start, end in intervals:
                    stream.write(f'{node},{link},{peer},{seconds(start)},{seconds(end)},1\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='new directory for three scenarios and handovers.csv')
    parser.add_argument('--state', type=Path, default=os.environ.get('STATE_EXAMPLE'),
                        required=not os.environ.get('STATE_EXAMPLE'))
    parser.add_argument('--routes', type=Path, help='override dynamic-state directory')
    parser.add_argument('--duration', type=nanoseconds, help='simulation seconds (default: full state)')
    parser.add_argument('--src', type=int, default=1584)
    parser.add_argument('--dst', type=int, default=1585)
    parser.add_argument('--timing', choices=['before', 'after'], default='before',
                        help='legacy default: before; new experiments use after')
    parser.add_argument('--handover-kind', choices=['all', 'gsl'], default='all')
    parser.add_argument('--windows-ms', type=int, nargs='+', default=[30, 100, 200])
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output already exists; choose a new directory')
    if any(ms < 0 for ms in args.windows_ms):
        parser.error('Loss windows must be nonnegative')
    try:
        routes, interval, duration, num_sats, node_count = load_state(args.state, args.routes, args.duration)
        if args.src == args.dst or not all(num_sats <= n < node_count for n in (args.src, args.dst)):
            raise ValueError('Source and destination must be distinct ground-station node IDs')
        events = detect_handovers(routes, interval, duration, args.src, args.dst, node_count)
        events = select_handovers(events, args.handover_kind)
        write_scenarios(args.output, events, duration, args.timing, args.windows_ms)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(f'Detected {len(events)} handover times; wrote {len(args.windows_ms)} loss scenarios to {args.output}')


if __name__ == '__main__':
    main()
