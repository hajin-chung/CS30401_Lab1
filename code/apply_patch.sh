#!/bin/bash
# Apply the submission's C++ diff to the course ns-3 tree, then build it.
# Requires NS3_DIR from the container. Accepts an already-applied patch; conflicting
# changes fail the preflight check. Never resets the source tree.
# Adds scenario initialization, a link-scenario helper, device capacity caps, and
# endpoint context for injected errors. TCP and routing algorithms are unchanged.
# After editing ns-3, export with make_patch.sh and inspect before committing:
# that course exporter stages all ns-3 changes.
set -euo pipefail
: "${NS3_DIR:?Run inside the course Docker image}"
CODE_DIR=$(cd "$(dirname "$0")" && pwd)
cd "$NS3_DIR"
if git apply --reverse --check "$CODE_DIR/ns3.patch" 2>/dev/null; then
    echo "Scenario patch is already applied."
else
    git apply --check "$CODE_DIR/ns3.patch"
    git apply "$CODE_DIR/ns3.patch"
fi
./ns3 build
