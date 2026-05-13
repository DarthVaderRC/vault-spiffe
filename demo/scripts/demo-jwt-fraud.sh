#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

ACTION="${1:-run}"

run_internal() {
  local command="$1"

  compose exec -T demo-tools bash -lc \
    "export PYTHONPATH=/workspace/demo/python FRAUD_WEB_URL=http://localhost:${FRAUD_WEB_PORT}/; python /workspace/demo/scripts/internal/fraud_jwt_demo.py '$command'"
}

run_flow() {
  local steps=(
    approle-login
    mint-jwt
    spiffe-jwt-auth
    db-creds
    final-reveal
  )
  local idx

  rm -f "$RUNTIME_DIR/checkpoints/fraud.json"

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
    echo "Usage: ./scripts/demo-jwt-fraud.sh [status|reset]" >&2
    exit 1
    ;;
esac

if [[ "$ACTION" == "run" ]]; then
  compose up -d --build demo-tools hashibank-fraud-web >/dev/null 2>&1
  wait_for_http "hashibank-fraud-web" "http://localhost:${FRAUD_WEB_PORT}/healthz"
else
  compose up -d --build demo-tools >/dev/null 2>&1
fi

if [[ "$ACTION" == "run" ]]; then
  run_flow
else
  run_internal "$ACTION"
fi

if [[ "$ACTION" == "run" ]]; then
  curl --silent --show-error --fail "http://localhost:${FRAUD_WEB_PORT}/api/demo" >/dev/null
fi
