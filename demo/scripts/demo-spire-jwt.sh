#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

ACTION="${1:-run}"

run_internal() {
  local command="$1"

  spire_client_exec \
    "export PYTHONPATH=/workspace/demo/python; python /workspace/demo/scripts/internal/spire_jwt_demo.py '$command'"
}

run_flow() {
  local steps=(
    fetch-jwt
    spiffe-jwt-auth
    kv-read
  )
  local idx

  rm -f "$RUNTIME_DIR/checkpoints/spire-jwt.json"

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
    echo "Usage: ./scripts/demo-spire-jwt.sh [status|reset]" >&2
    exit 1
    ;;
esac

require_spire_overlay_bootstrap

compose up -d --build "$VAULT_SERVICE" "$SPIRE_SERVER_SERVICE" "$SPIRE_AGENT_SERVICE" "$SPIRE_CLIENT_SERVICE" >/dev/null 2>&1
wait_for_vault_service "$VAULT_SERVICE"
wait_for_spire_bundle_endpoint
wait_for_spire_agent_api

if [[ "$ACTION" == "run" ]]; then
  run_flow
else
  run_internal "$ACTION"
fi
