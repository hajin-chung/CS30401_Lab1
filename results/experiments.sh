#!/bin/bash
# Run inside the patched course container: bash results/experiments.sh [output_dir]
# Creates 20 runs and their overview.png plots. Output paths are relative to the
# submission root; use a new output directory for each invocation.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
output_dir="${1:-results/ecn2_matrix}"
duration_s=200
congestion_file=code/scenarios/congestion_scenario2.txt
loss_file=code/scenarios/loss_scenario.txt

if (( $# > 1 )); then
    echo "Usage: bash results/experiments.sh [output_dir]" >&2
    exit 1
fi
if [[ -e "$output_dir" ]]; then
    echo "Output already exists: $output_dir. Choose a new directory." >&2
    exit 1
fi
: "${NS3_DIR:?Run inside the patched course container}"
for input in "$congestion_file" "$loss_file" results/plot_run.py \
    "$NS3_DIR/utils/verify-cubic-new-ecn.py"; do
    if [[ ! -f "$input" ]]; then
        echo "Missing input: $input" >&2
        exit 1
    fi
done
for executable in python3 run_sim.sh uv; do
    command -v "$executable" >/dev/null || {
        echo "Missing $executable; run inside the course container." >&2
        exit 1
    }
done
mkdir -p "$output_dir"

variants=(newreno:TcpNewReno cubic:TcpCubic cubic-new:TcpCubicNew bbr:TcpBbr vegas:TcpVegas)
scenarios=(baseline congestion loss combined)
for variant in "${variants[@]}"; do
    name="${variant%%:*}"
    tcp="${variant#*:}"
    for scenario in "${scenarios[@]}"; do
        options=()
        case "$scenario" in
            congestion) options+=(--congestion "$congestion_file") ;;
            loss)       options+=(--loss "$loss_file") ;;
            combined)   options+=(--congestion "$congestion_file" --loss "$loss_file") ;;
        esac
        run_dir="$output_dir/$name-$scenario"
        printf '\nRunning %s / %s\n' "$tcp" "$scenario"
        python3 code/prepare_run.py "$run_dir" \
            --cc "$tcp" --duration "$duration_s" \
            --rate-mbps 100 --queue-pkts 100 --seed 123456789 \
            "${options[@]}"
        run_sim.sh "$run_dir"
        if [[ "$tcp" == TcpCubicNew ]]; then
            python3 "$NS3_DIR/utils/verify-cubic-new-ecn.py" "$run_dir"
        fi
        uv run results/plot_run.py "$run_dir"
    done
done

printf '\nFinished all %d experiments: %s\n' \
    "$(( ${#variants[@]} * ${#scenarios[@]} ))" "$output_dir"
