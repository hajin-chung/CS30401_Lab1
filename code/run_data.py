"""Shared readers and time-based measurements for ns-3 run logs."""
from bisect import bisect_right
import csv
import json
import math
from pathlib import Path


def rows(path, headers=True):
    with Path(path).open() as stream:
        yield from (csv.DictReader(stream) if headers else csv.reader(stream))


def properties(path):
    result = {}
    for line in path.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            result[key.strip()] = value.strip().strip('"')
    return result


def trace(logs, flow, metric):
    times, values = [], []
    for row in rows(logs / f'tcp_flow_{flow}_{metric}.csv', False):
        t, value = int(row[1]) / 1e9, int(row[2])
        if times and t < times[-1]:
            raise ValueError(f'Unsorted {metric} trace for flow {flow}')
        if metric == 'progress' and values and value < values[-1]:
            raise ValueError(f'Decreasing progress for flow {flow}')
        times.append(t)
        values.append(value)
    if not times:
        raise ValueError(f'Empty {metric} trace for flow {flow}')
    return times, values


def at(series, t, default=0):
    times, values = series
    i = bisect_right(times, t) - 1
    return values[i] if i >= 0 else default


def edges(start, end, width):
    return [start + i * width for i in range(math.ceil((end - start) / width))] + [end]


def goodput(series, bounds):
    totals = [at(series, t) for t in bounds]
    return [(b - a) * 8e-6 / (right - left)
            for a, b, left, right in zip(totals, totals[1:], bounds, bounds[1:])]


def time_stats(series, start, end, scale=1):
    times, values = series
    points = [start, *(t for t in times if start < t < end), end]
    samples = []
    for a, b in zip(points, points[1:]):
        value = at(series, a, None)
        if value is not None and value > 0 and b > a:
            samples.append((value * scale, b - a))
    total = sum(w for _, w in samples)
    if not total:
        return None, None
    mean = sum(v * w for v, w in samples) / total
    cumulative, p95 = 0, None
    for value, weight in sorted(samples):
        cumulative += weight
        if cumulative >= total * 0.95:
            p95 = value
            break
    return mean, p95


def jain(values):
    denominator = len(values) * sum(v * v for v in values)
    return sum(values) ** 2 / denominator if len(values) > 1 and denominator else None


class Run:
    def __init__(self, path, require_finished=True):
        self.path = Path(path).resolve()
        if self.path.name == 'logs_ns3':
            self.path = self.path.parent
        self.logs = self.path / 'logs_ns3'
        if require_finished:
            finished = self.logs / 'finished.txt'
            if not finished.exists() or finished.read_text().strip() != 'Yes':
                raise ValueError(f'Incomplete simulation: {self.path}')
        self.config = properties(self.path / 'config_ns3.properties')
        self.duration = int(self.config['simulation_end_time_ns']) / 1e9
        self.schedule = {int(r[0]): {'src': int(r[1]), 'dst': int(r[2]), 'size': int(r[3]), 'start': int(r[4]) / 1e9}
                         for r in rows(self.path / self.config.get('tcp_flow_schedule_filename', 'schedule.csv'), False)}
        meta = self.path / 'experiment.json'
        self.meta = json.loads(meta.read_text()) if meta.exists() else {}
        ep = self.path / 'epochs.csv'
        self.epochs = [{k: float(v) for k, v in row.items()} for row in rows(ep)] if ep.exists() else []
        handovers = self.path / 'handovers.csv'
        self.handovers = sorted({float(r['time_s']) for r in rows(handovers)}) if handovers.exists() else []

    def scenario(self, key):
        name = self.config.get(key)
        if not name:
            return 0, []
        base, entries = 0, []
        with (self.path / name).open() as stream:
            for line in stream:
                line = line.split('#', 1)[0].strip()
                if not line:
                    continue
                if ',' not in line:
                    base = float(line.split('=')[-1])
                else:
                    r = next(csv.reader([line]))
                    entries.append((r[0], r[1], r[2], float(r[3]), float(r[4]), float(r[5])))
        return base, entries

    def capacity_mbit(self, start, end):
        if not self.epochs:
            return None
        return sum(max(0, min(end, e['end_s']) - max(start, e['start_s'])) * e['capacity_mbps']
                   for e in self.epochs)
