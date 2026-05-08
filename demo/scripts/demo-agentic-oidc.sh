#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

ACTION="${1:-all}"

case "$ACTION" in
  approle-login|mint-jwt|fetch-discovery|validate-jwt|final-reveal|all|status|reset)
    ;;
  *)
    echo "Usage: ./scripts/demo-agentic-oidc.sh [approle-login|mint-jwt|fetch-discovery|validate-jwt|final-reveal|all|status|reset]" >&2
    exit 1
    ;;
esac

if [[ "$ACTION" == "final-reveal" || "$ACTION" == "all" ]]; then
  compose up -d demo-tools hashibank-assistant >/dev/null
  wait_for_http "hashibank-assistant" "http://localhost:${ASSISTANT_WEB_PORT}/healthz"
else
  compose up -d demo-tools >/dev/null
fi

compose exec -T demo-tools bash -lc \
  "export PYTHONPATH=/workspace/demo/python ASSISTANT_WEB_URL=http://localhost:${ASSISTANT_WEB_PORT}/; python /workspace/demo/scripts/internal/assistant_oidc_demo.py '$ACTION'"

if [[ "$ACTION" == "final-reveal" || "$ACTION" == "all" ]]; then
  curl --silent --show-error --fail "http://localhost:${ASSISTANT_WEB_PORT}/api/demo" >/dev/null
fi
