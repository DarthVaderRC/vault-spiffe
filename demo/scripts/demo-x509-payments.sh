#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

ACTION="${1:-all}"

case "$ACTION" in
  approle-login|pki-issue|spiffe-x509-auth|payments-api-kv-secrets|all|status|reset)
    ;;
  *)
    echo "Usage: ./scripts/demo-x509-payments.sh [approle-login|pki-issue|spiffe-x509-auth|payments-api-kv-secrets|all|status|reset]" >&2
    exit 1
    ;;
esac

compose up -d --build demo-tools >/dev/null
compose exec -T demo-tools bash -lc \
  "export PYTHONPATH=/workspace/demo/python; python /workspace/demo/scripts/internal/payments_x509_demo.py '$ACTION'"
