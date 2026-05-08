#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

ACTION="${1:-all}"

case "$ACTION" in
  approle-login|mint-jwt|spiffe-jwt-auth|db-creds|final-reveal|all|status|reset)
    ;;
  *)
    echo "Usage: ./scripts/demo-jwt-fraud.sh [approle-login|mint-jwt|spiffe-jwt-auth|db-creds|final-reveal|all|status|reset]" >&2
    exit 1
    ;;
esac

if [[ "$ACTION" == "final-reveal" || "$ACTION" == "all" ]]; then
  compose up -d --build demo-tools hashibank-fraud-web >/dev/null
  wait_for_http "hashibank-fraud-web" "http://localhost:${FRAUD_WEB_PORT}/healthz"
else
  compose up -d --build demo-tools >/dev/null
fi

compose exec -T demo-tools bash -lc \
  "export PYTHONPATH=/workspace/demo/python FRAUD_WEB_URL=http://localhost:${FRAUD_WEB_PORT}/; python /workspace/demo/scripts/internal/fraud_jwt_demo.py '$ACTION'"

if [[ "$ACTION" == "final-reveal" || "$ACTION" == "all" ]]; then
  curl --silent --show-error --fail "http://localhost:${FRAUD_WEB_PORT}/api/demo" >/dev/null
fi
