#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

ACTION="${1:-run}"

run_internal() {
  local command="$1"

  compose exec -T demo-tools bash -lc \
    "export PYTHONPATH=/workspace/demo/python; python /workspace/demo/scripts/internal/payments_x509_demo.py '$command'"
}

run_flow() {
  local steps=(
    approle-login
    pki-issue
    spiffe-x509-auth
    payments-api-kv-secrets
  )
  local idx

  rm -f "$RUNTIME_DIR/checkpoints/payments.json"

  for idx in "${!steps[@]}"; do
    run_internal "${steps[$idx]}"
    if (( idx + 1 < ${#steps[@]} )); then
      pause_for_continue
    fi
  done
}

case "$ACTION" in
  run|status|reset)
    ;;
  *)
    echo "Usage: ./scripts/demo-x509-payments.sh [status|reset]" >&2
    exit 1
    ;;
esac

compose up -d --build demo-tools >/dev/null 2>&1
if [[ "$ACTION" == "run" ]]; then
  run_flow
else
  run_internal "$ACTION"
fi
