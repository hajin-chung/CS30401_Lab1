#!/bin/bash
# Apply ECN2 on the course baseline or submission 466fd6e, then build.
# Already-applied patches are accepted; conflicts leave source files untouched.
set -euo pipefail
: "${NS3_DIR:?Run inside the course Docker image}"
PATCH_DIR=$(cd "$(dirname "$0")/patch" && pwd)
cd "$NS3_DIR"
if git apply --reverse --check "$PATCH_DIR/ns3.patch" 2>/dev/null; then
    echo "Merged ns-3 patch is already applied."
elif git apply --check "$PATCH_DIR/ns3.patch" 2>/dev/null; then
    git apply "$PATCH_DIR/ns3.patch"
elif git apply --check "$PATCH_DIR/ecn2-upgrade.patch" 2>/dev/null; then
    git apply "$PATCH_DIR/ecn2-upgrade.patch"
else
    echo "Cannot apply the merged patch to this source tree." >&2
    echo "Use a fresh course ns-3.48 tree or resolve local edits first (see README)." >&2
    exit 1
fi
./ns3 build
