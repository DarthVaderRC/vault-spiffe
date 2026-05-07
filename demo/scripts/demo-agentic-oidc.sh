#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

compose up -d hashibank-assistant >/dev/null

for _ in $(seq 1 30); do
  if curl --silent --show-error "http://localhost:${ASSISTANT_WEB_PORT}/healthz" >/dev/null 2>&1; then
    curl --silent --show-error "http://localhost:${ASSISTANT_WEB_PORT}/api/demo"
    echo
    exit 0
  fi
  sleep 2
done

echo "hashibank-assistant did not become ready in time" >&2
exit 1
