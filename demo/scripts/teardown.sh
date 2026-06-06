#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

if command_exists kind && kind get clusters 2>/dev/null | grep -Fxq "$KIND_CLUSTER_NAME"; then
  kind delete cluster --name "$KIND_CLUSTER_NAME" >/dev/null
fi

compose down -v --remove-orphans >/dev/null

find "$RUNTIME_DIR" -mindepth 1 ! -name ".gitignore" -exec rm -rf {} +
find "$TLS_DIR" -mindepth 1 ! -name ".gitignore" -exec rm -rf {} +

cat <<EOF
HashiBank demo stack stopped and local generated artifacts removed.

To start again from a clean state:
  ./scripts/bootstrap.sh
  ./scripts/bootstrap.sh spire
EOF
