#!/usr/bin/env bash
set -euo pipefail

COMMON_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SCRIPTS_DIR=$(cd "$COMMON_DIR/.." && pwd)
DEMO_DIR=$(cd "$SCRIPTS_DIR/.." && pwd)
COMPOSE_FILE="$DEMO_DIR/docker-compose.yml"
RUNTIME_DIR="$DEMO_DIR/runtime"
TLS_DIR="$DEMO_DIR/config/tls"
ROOT_CA_FILE="$TLS_DIR/hashibank-root-ca.crt"
IDENTITY_HOST_PORT="${HASHIBANK_IDENTITY_HOST_PORT:-18200}"
ACCESS_HOST_PORT="${HASHIBANK_ACCESS_HOST_PORT:-18300}"
FRAUD_WEB_PORT="${HASHIBANK_FRAUD_WEB_PORT:-18081}"
ASSISTANT_WEB_PORT="${HASHIBANK_ASSISTANT_WEB_PORT:-18082}"
IDENTITY_HOST_ADDR="https://localhost:${IDENTITY_HOST_PORT}"
ACCESS_HOST_ADDR="https://localhost:${ACCESS_HOST_PORT}"

compose() {
  docker compose -f "$COMPOSE_FILE" "$@"
}

vault_exec() {
  local service="$1"
  shift
  # Run Vault CLI commands inside the service container so they use container-local
  # DNS names and the demo CA bundle instead of host networking assumptions.
  compose exec -T "$service" sh -lc "export VAULT_ADDR=https://127.0.0.1:8200 VAULT_CACERT=/vault/config/tls/hashibank-root-ca.crt; $*"
}

wait_for_https() {
  local name="$1"
  local base_url="$2"

  for _ in $(seq 1 60); do
    if curl --silent --show-error --cacert "$ROOT_CA_FILE" "$base_url/v1/sys/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done

  echo "Timed out waiting for $name at $base_url" >&2
  return 1
}

wait_for_postgres() {
  for _ in $(seq 1 60); do
    if compose exec -T postgres-hashibank pg_isready -U postgres -d hashibank >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done

  echo "Timed out waiting for postgres-hashibank" >&2
  return 1
}

read_status_value() {
  local service="$1"
  local field="$2"

  vault_exec "$service" "vault status" | awk -v target="$field" '$1 == target {print $2}'
}
