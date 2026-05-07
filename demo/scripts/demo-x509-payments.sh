#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

compose up -d demo-tools >/dev/null
compose exec -T demo-tools bash -lc "export PYTHONPATH=/workspace/demo/python; python /workspace/demo/scripts/internal/payments_x509_demo.py"
