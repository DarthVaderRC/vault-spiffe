#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

ACTION="${1:-run}"

run_internal() {
  local command="$1"

  compose exec -T demo-tools bash -lc \
    "export PYTHONPATH=/workspace/demo/python ASSISTANT_WEB_URL=http://localhost:${ASSISTANT_WEB_PORT}/; python /workspace/demo/scripts/internal/assistant_oidc_demo.py '$command'"
}

run_flow() {
  local steps=(
    approle-login
    mint-jwt
    fetch-discovery
    validate-jwt
    final-reveal
  )
  local idx

  rm -f "$RUNTIME_DIR/checkpoints/assistant.json"

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
    echo "Usage: ./scripts/demo-agentic-oidc.sh [status|reset]" >&2
    exit 1
    ;;
esac

if [[ "$ACTION" == "run" ]]; then
  compose up -d --build demo-tools hashibank-assistant >/dev/null 2>&1
  wait_for_http "hashibank-assistant" "http://localhost:${ASSISTANT_WEB_PORT}/healthz"
else
  compose up -d --build demo-tools >/dev/null 2>&1
fi

if [[ "$ACTION" == "run" ]]; then
  run_flow
else
  run_internal "$ACTION"
fi

if [[ "$ACTION" == "run" ]]; then
  curl --silent --show-error --fail "http://localhost:${ASSISTANT_WEB_PORT}/api/demo" >/dev/null
fi
