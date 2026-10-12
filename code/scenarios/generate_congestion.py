"""Generate source-GSL capacity caps for epochs between handovers."""
import argparse
import csv
import math
from pathlib import Path
import random

if __package__:
    from .generate_handover_loss import nanoseconds, seconds
else:
    from generate_handover_loss import nanoseconds, seconds

PROFILES = ('constant', 'drop', 'rise', 'alternating', 'random')


def epoch_bounds(handovers, duration_ns):
    with Path(handovers).open() as stream:
        times = {nanoseconds(row['time_s']) for row in csv.DictReader(stream)}
    if any(t < 0 or t > duration_ns for t in times):
        raise ValueError('Handover times must fit the simulation duration')
    return [0, *sorted(t for t in times if 0 < t < duration_ns), duration_ns]


def capacities(count, profile, low, high, seed, values=None):
    rng = random.Random(seed)
    if values:
        return [values[i % len(values)] for i in range(count)]
    if profile == 'constant':
        return [high] * count
    if profile == 'drop':
        return [high] + [low] * (count - 1)
    if profile == 'rise':
        return [low] + [high] * (count - 1)
    if profile == 'alternating':
        return [high if i % 2 == 0 else low for i in range(count)]
    return [round(rng.uniform(low, high), 6) for _ in range(count)]


def generate(output, handovers, duration_ns, source, profile, low, high, seed, values=None):
    bounds = epoch_bounds(handovers, duration_ns)
    rates = capacities(len(bounds) - 1, profile, low, high, seed, values)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    with (output / 'epochs.csv').open('w', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['epoch', 'start_s', 'end_s', 'capacity_mbps'])
        writer.writerows((i, seconds(a), seconds(b), rate)
                         for i, (a, b, rate) in enumerate(zip(bounds, bounds[1:], rates)))
    with (output / 'congestion_scenario.txt').open('w') as stream:
        stream.write('# Source outgoing GSL capacity; retained queues, [start,end).\n')
        for a, b, rate in zip(bounds, bounds[1:], rates):
            stream.write(f'{source},gsl,*,{seconds(a)},{seconds(b)},{rate}\n')
    return len(rates)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='new scenario directory')
    parser.add_argument('--handovers', required=True, type=Path)
    parser.add_argument('--duration', required=True, type=nanoseconds)
    parser.add_argument('--src', type=int, default=1584)
    parser.add_argument('--profile', choices=PROFILES, default='alternating')
    parser.add_argument('--low-mbps', type=float, default=20)
    parser.add_argument('--high-mbps', type=float, default=100)
    parser.add_argument('--seed', type=int, default=123456789)
    parser.add_argument('--capacities', nargs='+', type=float, help='explicit per-epoch rates, repeated cyclically')
    args = parser.parse_args()
    rates = [args.low_mbps, args.high_mbps, *(args.capacities or [])]
    if args.duration <= 0 or args.src < 0 or any(not math.isfinite(v) or v <= 0 for v in rates):
        parser.error('Duration and capacities must be positive and finite; source must be nonnegative')
    if args.low_mbps > args.high_mbps:
        parser.error('--low-mbps must not exceed --high-mbps')
    try:
        count = generate(args.output, args.handovers, args.duration, args.src, args.profile,
                         args.low_mbps, args.high_mbps, args.seed, args.capacities)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(f'Wrote {count} epochs to {args.output}')


if __name__ == '__main__':
    main()
