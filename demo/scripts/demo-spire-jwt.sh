#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

ACTION="${1:-run}"
FRAUD_WEB_SCENARIO="spire-jwt"

run_internal() {
  local command="$1"
  local pause_env="${2:-}"

  spire_client_exec \
    "export PYTHONPATH=/workspace/demo/python FRAUD_WEB_URL=http://localhost:${FRAUD_WEB_PORT}/ ${pause_env}; python /workspace/demo/scripts/internal/spire_jwt_demo.py '$command'"
}

run_flow() {
  local steps=(
    fetch-jwt
    spiffe-jwt-auth
    db-creds
    final-reveal
  )
  local idx

  rm -f "$RUNTIME_DIR/checkpoints/spire-jwt.json"

  for idx in "${!steps[@]}"; do
    run_internal "${steps[$idx]}" "$(demo_pause_env "$idx")"
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

compose up -d --build "$VAULT_SERVICE" "$SPIRE_SERVER_SERVICE" "$SPIRE_AGENT_SERVICE" "$SPIRE_CLIENT_SERVICE" postgres-hashibank demo-tools >/dev/null 2>&1
wait_for_vault_service "$VAULT_SERVICE"
wait_for_spire_bundle_endpoint
wait_for_spire_agent_api
wait_for_postgres

if [[ "$ACTION" == "run" ]]; then
  HASHIBANK_DEMO_SCENARIO="$FRAUD_WEB_SCENARIO" compose up -d --build --force-recreate hashibank-fraud-web >/dev/null 2>&1
  wait_for_http "hashibank-fraud-web" "http://localhost:${FRAUD_WEB_PORT}/healthz"
fi

if [[ "$ACTION" == "run" ]]; then
  run_flow
else
  run_internal "$ACTION"
fi

if [[ "$ACTION" == "run" ]]; then
  curl --silent --show-error --fail "http://localhost:${FRAUD_WEB_PORT}/api/demo" >/dev/null
fi
