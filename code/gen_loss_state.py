"""Generate seeded per-satellite burst timelines using only the standard library.

Reads tles.txt and the loss properties; does not change orbital/routing state or
run ns-3. Writes loss_events.csv (satellite_id,start_ns,end_ns) and metadata in
loss_model.properties: parameters, duration, format version, count, and TLE hash.

For one-second peak probability q, normal-state waiting times are exponential
with rate -log(1-q). Peak durations are uniform between the configured bounds.
Peaks cannot overlap on one satellite; the final peak is clipped to the duration.
Each satellite has a seed-derived independent stream, so changing the selection
does not shift another satellite's events. Timelines are independent of packet
arrivals and reusable across TCP variants. ns-3's simulation_seed separately
controls packet-error draws; individual drops may differ across TCPs.

Use a new output directory and a duration covering the later run. These are
illustrative residual-error events, not a calibrated vibration model. See the
comments in scenarios/loss_model.properties for controls and runtime semantics.
"""
import argparse
import csv
import hashlib
import math
from pathlib import Path
import random


def properties(path):
    result = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            if key.strip() in result:
                raise ValueError(f"duplicate property: {key}")
            result[key.strip()] = value.strip()
    return result


def generate(state, config, duration, output):
    state, output = Path(state), Path(output)
    params = properties(config)
    required = {"event_seed", "satellites", "link_types", "base_loss_probability",
                "peak_loss_probability", "peak_probability_per_second",
                "peak_duration_min_ms", "peak_duration_max_ms"}
    if set(params) != required:
        raise ValueError(f"missing keys: {required - set(params)}; unknown keys: {set(params) - required}")
    seed = int(params["event_seed"])
    shape = state.joinpath("tles.txt").read_text().splitlines()[0].split()
    n = int(shape[0]) * int(shape[1])
    sats = list(range(n)) if params["satellites"] == "all" else sorted({int(v) for v in params["satellites"].split(",")})
    types = params["link_types"].split(",")
    base = float(params["base_loss_probability"])
    peak = float(params["peak_loss_probability"])
    q = float(params["peak_probability_per_second"])
    low = float(params["peak_duration_min_ms"]) / 1000
    high = float(params["peak_duration_max_ms"]) / 1000
    if not all(math.isfinite(x) for x in (duration, base, peak, q, low, high)):
        raise ValueError("parameters must be finite")
    if seed < 0 or seed >= 2**64 or not sats or any(s < 0 or s >= n for s in sats):
        raise ValueError("invalid seed or satellite selection")
    if not set(types) <= {"isl", "gsl"} or len(types) != len(set(types)):
        raise ValueError("link_types must be isl, gsl, or isl,gsl")
    if not (0 <= base <= peak <= 1 and 0 <= q < 1 and 1e-9 <= low <= high and duration > 0):
        raise ValueError("require 0 <= base <= peak <= 1; 0 <= q < 1; positive duration bounds")
    duration_ns = round(duration * 1e9)
    if duration_ns <= 0 or duration_ns >= 2**63:
        raise ValueError("duration is outside ns-3 time range")
    events = []
    if q:
        hazard = -math.log1p(-q)
        for satellite in sats:
            # Stable per-satellite streams: changing the selection cannot shift other satellites' events.
            digest = hashlib.sha256(f"{seed}:{satellite}".encode()).digest()
            rng = random.Random(int.from_bytes(digest, "big"))
            time_ns = 0
            while time_ns < duration_ns:
                start = time_ns + max(1, round(rng.expovariate(hazard) * 1e9))
                if start >= duration_ns:
                    break
                end = min(duration_ns, start + max(1, round(rng.uniform(low, high) * 1e9)))
                events.append((satellite, start, end))
                time_ns = end
    output.mkdir(parents=True, exist_ok=False)
    with (output / "loss_events.csv").open("w", newline="") as stream:
        stream.write("# satellite_id,start_ns,end_ns\n")
        csv.writer(stream, lineterminator="\n").writerows(events)
    metadata = dict(params, format_version="1", duration_ns=str(duration_ns), num_satellites=str(n),
                    tles_sha256=hashlib.sha256((state / "tles.txt").read_bytes()).hexdigest())
    (output / "loss_model.properties").write_text("".join(f"{k}={v}\n" for k, v in sorted(metadata.items())))
    print(f"Generated {len(events)} events for {len(sats)} satellites: {output}")
    return events


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state", type=Path)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        generate(args.state, args.config, args.duration, args.output)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
