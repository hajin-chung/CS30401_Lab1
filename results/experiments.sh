#!/bin/bash
# Run inside the patched course container: bash results/experiments.sh [output_dir]
# Runs four scenarios at a time for each TCP variant, including verification
# and plots. Use a new output directory, relative to the submission root.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
output_dir="${1:-results/ecn2_matrix}"
duration_s=200
congestion_file=code/scenarios/congestion_scenario2.txt
loss_file=code/scenarios/loss_200ms.txt

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
for executable in python3 uv setsid; do
    command -v "$executable" >/dev/null || {
        echo "Missing $executable; run inside the course container." >&2
        exit 1
    }
done
mkdir -p "$output_dir"
output_dir=$(cd "$output_dir" && pwd)

run_experiment() {
    local tcp=$1 scenario=$2 run_dir=$3 duration_s=$4 congestion_file=$5 loss_file=$6
    local options=()
    case "$scenario" in
        congestion) options+=(--congestion "$congestion_file") ;;
        loss)       options+=(--loss "$loss_file") ;;
        combined)   options+=(--congestion "$congestion_file" --loss "$loss_file") ;;
    esac
    python3 code/prepare_run.py "$run_dir" \
        --cc "$tcp" --duration "$duration_s" \
        --rate-mbps 100 --queue-pkts 100 --seed 123456789 \
        "${options[@]}"
    (cd "$NS3_DIR" && ./ns3 run "main_satnet --run_dir=$run_dir" --no-build) \
        > "$run_dir/out.txt" 2>&1
    if [[ "$tcp" == TcpCubicNew ]]; then
        python3 "$NS3_DIR/utils/verify-cubic-new-ecn.py" "$run_dir"
    fi
    uv run results/plot_run.py "$run_dir"
}
export -f run_experiment

pids=()
cleanup() {
    local pid
    for pid in "${pids[@]}"; do
        [[ -n "$pid" ]] || continue
        kill -TERM -- "-$pid" 2>/dev/null || true
    done
    for pid in "${pids[@]}"; do
        [[ -n "$pid" ]] || continue
        wait "$pid" 2>/dev/null || true
    done
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

variants=(cubic:TcpCubic cubic-new:TcpCubicNew bbr:TcpBbr)
scenarios=(baseline congestion loss combined)
for variant in "${variants[@]}"; do
    name="${variant%%:*}"
    tcp="${variant#*:}"
    pids=()
    for scenario in "${scenarios[@]}"; do
        run_dir="$output_dir/$name-$scenario"
        printf 'Starting %s / %s\n' "$tcp" "$scenario"
        setsid bash -euo pipefail -c 'run_experiment "$@"' _ \
            "$tcp" "$scenario" "$run_dir" "$duration_s" "$congestion_file" "$loss_file" \
            > "$run_dir.driver.log" 2>&1 &
        pids+=("$!")
    done
    failed=0
    for i in "${!pids[@]}"; do
        if wait "${pids[$i]}"; then
            printf 'Finished %s / %s\n' "$tcp" "${scenarios[$i]}"
        else
            printf 'Failed %s / %s; see %s/%s-%s.driver.log and its out.txt\n' \
                "$tcp" "${scenarios[$i]}" "$output_dir" "$name" "${scenarios[$i]}" >&2
            failed=1
        fi
        pids[$i]=''
    done
    (( failed == 0 )) || exit 1
done

printf '\nFinished all %d experiments: %s\n' \
    "$(( ${#variants[@]} * ${#scenarios[@]} ))" "$output_dir"
