#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

ACTION="${1:-run}"

PRIMARY_SERVICE="$VAULT_SERVICE"
PRIMARY_HOST_ADDR="$VAULT_HOST_ADDR"
PRIMARY_ROOT_TOKEN_FILE="$ROOT_TOKEN_FILE"

REPLICA_SERVICE="$PERF_VAULT_SERVICE"
REPLICA_HOST_ADDR="$PERF_VAULT_HOST_ADDR"
REPLICA_RUNTIME_DIR="$PERF_VAULT_RUNTIME_DIR"
REPLICA_RUNTIME_STORAGE_DIR="$PERF_VAULT_RUNTIME_STORAGE_DIR"
REPLICA_ROOT_TOKEN_FILE="$PERF_ROOT_TOKEN_FILE"

TEST_MOUNT="spiffe-default-issuer"
TEST_POLICY="perf-repl-spiffe-issuer"
TEST_ROLE="perf-repl-spiffe-issuer"
TEST_APPROLE="perf-repl-spiffe-issuer"
TEST_AUDIENCE="perf-replica-issuer-check"
TEST_SPIFFE_PATH="replication/issuer-check"
TEST_TRUST_DOMAIN="hashibank.demo"
REPLICA_ID="perf-spiffe-issuer-check"

POLICY_FILE="$RUNTIME_DIR/generated/${TEST_POLICY}.hcl"
APPROLE_PAYLOAD_FILE="$RUNTIME_DIR/generated/${TEST_APPROLE}.json"
TEMPLATE_FILE="$RUNTIME_DIR/templates/${TEST_ROLE}-template.json"
ACTIVATION_TOKEN_FILE="$REPLICA_RUNTIME_DIR/secondary-activation-token"
RESULT_FILE="$RUNTIME_DIR/generated/perf-repl-spiffe-issuer-result.json"

json_get() {
  local path="$1"

  python3 -c '
import json
import sys

parts = [part for part in sys.argv[1].split(".") if part]
value = json.load(sys.stdin)
for part in parts:
    if isinstance(value, list):
        value = value[int(part)]
    else:
        value = value.get(part)
    if value is None:
        break
if isinstance(value, (dict, list)):
    print(json.dumps(value))
elif value is None:
    print("")
else:
    print(value)
' "$path"
}

replication_status() {
  local base_url="$1"

  curl --silent --show-error --fail \
    --cacert "$ROOT_CA_FILE" \
    "$base_url/v1/sys/replication/performance/status"
}

ensure_primary_demo_bootstrap() {
  local root_token

  if [[ -f "$PRIMARY_ROOT_TOKEN_FILE" && -f "$VAULT_RUNTIME_DIR/init.txt" ]]; then
    compose up -d --build "$PRIMARY_SERVICE" postgres-hashibank demo-tools >/dev/null 2>&1
    wait_for_vault_service "$PRIMARY_SERVICE"
    wait_for_postgres
    initialise_and_unseal_vault_service "$PRIMARY_SERVICE"

    root_token=$(<"$PRIMARY_ROOT_TOKEN_FILE")
    if vault_exec "VAULT_TOKEN=$root_token vault read spiffe/config >/dev/null 2>&1"; then
      return 0
    fi
  fi

  echo "Bootstrapping hashibank-vault so the performance replica uses the same primary cluster as the demo..."
  HASHIBANK_DEMO_NO_PAUSE=1 "$SCRIPT_DIR/bootstrap.sh"
}

enable_performance_primary() {
  local root_token
  local mode

  root_token=$(<"$PRIMARY_ROOT_TOKEN_FILE")
  mode=$(replication_status "$PRIMARY_HOST_ADDR" | json_get "data.mode")
  if [[ "$mode" == "primary" ]]; then
    return 0
  fi

  echo "Enabling performance replication primary on $PRIMARY_SERVICE..."
  vault_exec_service "$PRIMARY_SERVICE" \
    "VAULT_TOKEN=$root_token vault write -f sys/replication/performance/primary/enable" >/dev/null
}

generate_secondary_activation_token() {
  local root_token
  local raw_response
  local activation_token

  root_token=$(<"$PRIMARY_ROOT_TOKEN_FILE")
  raw_response=$(vault_exec_service "$PRIMARY_SERVICE" \
    "VAULT_TOKEN=$root_token vault write -format=json sys/replication/performance/primary/secondary-token id=$REPLICA_ID")
  activation_token=$(printf '%s' "$raw_response" | python3 -c '
import json
import sys

payload = json.load(sys.stdin)
token = payload.get("wrap_info", {}).get("token")
if not token:
    token = payload.get("data", {}).get("token")
if not token:
    raise SystemExit("No secondary activation token found in primary response")
print(token)
')
  printf '%s' "$activation_token" >"$ACTIVATION_TOKEN_FILE"
}

enable_performance_secondary() {
  local secondary_root_token
  local mode

  mode=$(replication_status "$REPLICA_HOST_ADDR" | json_get "data.mode")
  if [[ "$mode" == "secondary" ]]; then
    return 0
  fi

  if [[ ! -f "$ACTIVATION_TOKEN_FILE" ]]; then
    generate_secondary_activation_token
  fi

  secondary_root_token=$(<"$REPLICA_ROOT_TOKEN_FILE")

  echo "Enabling performance replication secondary on $REPLICA_SERVICE..."
  vault_exec_service "$REPLICA_SERVICE" \
    "VAULT_TOKEN=$secondary_root_token vault write sys/replication/performance/secondary/enable token=$(<"$ACTIVATION_TOKEN_FILE") primary_api_addr=https://hashibank-vault:8200 ca_file=/vault/config/tls/hashibank-root-ca.crt" >/dev/null
}

wait_for_replication_ready() {
  local primary_status
  local replica_status
  local primary_mode
  local primary_state
  local replica_mode
  local replica_state
  local replica_connection

  for _ in $(seq 1 60); do
    primary_status=$(replication_status "$PRIMARY_HOST_ADDR" 2>/dev/null || true)
    replica_status=$(replication_status "$REPLICA_HOST_ADDR" 2>/dev/null || true)

    if [[ -z "$primary_status" || -z "$replica_status" ]]; then
      sleep 2
      continue
    fi

    primary_mode=$(printf '%s' "$primary_status" | json_get "data.mode")
    primary_state=$(printf '%s' "$primary_status" | json_get "data.state")
    replica_mode=$(printf '%s' "$replica_status" | json_get "data.mode")
    replica_state=$(printf '%s' "$replica_status" | json_get "data.state")
    replica_connection=$(printf '%s' "$replica_status" | json_get "data.connection_state")

    if [[ "$primary_mode" == "primary" && "$primary_state" == "running" && "$replica_mode" == "secondary" && "$replica_connection" == "ready" ]]; then
      return 0
    fi
    sleep 2
  done

  echo "Timed out waiting for performance replication readiness" >&2
  echo "Primary status:" >&2
  printf '%s\n' "$primary_status" >&2
  echo "Replica status:" >&2
  printf '%s\n' "$replica_status" >&2
  return 1
}

configure_test_mount() {
  local root_token
  local approle_accessor

  root_token=$(<"$PRIMARY_ROOT_TOKEN_FILE")

  if ! vault_exec_service "$PRIMARY_SERVICE" "VAULT_TOKEN=$root_token vault auth list | grep -q '^approle/'"; then
    vault_exec_service "$PRIMARY_SERVICE" "VAULT_TOKEN=$root_token vault auth enable approle" >/dev/null
  fi

  cat >"$POLICY_FILE" <<EOF
path "${TEST_MOUNT}/config" {
  capabilities = ["read"]
}

path "${TEST_MOUNT}/role/${TEST_ROLE}/mintjwt" {
  capabilities = ["update"]
}
EOF
  vault_exec_service "$PRIMARY_SERVICE" \
    "VAULT_TOKEN=$root_token vault policy write ${TEST_POLICY} /vault/runtime/generated/${TEST_POLICY}.hcl" >/dev/null

  approle_accessor=$(vault_exec_service "$PRIMARY_SERVICE" "VAULT_TOKEN=$root_token vault auth list -format=json" | python3 -c '
import json
import sys

payload = json.load(sys.stdin)
entry = payload.get("approle/")
if not entry:
    raise SystemExit("AppRole auth mount not found after enable")
print(entry["accessor"])
')

  if vault_exec_service "$PRIMARY_SERVICE" "VAULT_TOKEN=$root_token vault read ${TEST_MOUNT}/config >/dev/null 2>&1"; then
    vault_exec_service "$PRIMARY_SERVICE" "VAULT_TOKEN=$root_token vault secrets disable ${TEST_MOUNT}" >/dev/null
  fi

  vault_exec_service "$PRIMARY_SERVICE" \
    "VAULT_TOKEN=$root_token vault secrets enable -path=${TEST_MOUNT} spiffe" >/dev/null
  vault_exec_service "$PRIMARY_SERVICE" \
    "VAULT_TOKEN=$root_token vault write ${TEST_MOUNT}/config trust_domain=${TEST_TRUST_DOMAIN} jwt_oidc_compatibility_mode=true key_lifetime=24h bundle_refresh_hint=1h" >/dev/null

  cat >"$TEMPLATE_FILE" <<EOF
{"sub":"spiffe://${TEST_TRUST_DOMAIN}/{{identity.entity.aliases.${approle_accessor}.custom_metadata.spiffe_path}}","experiment":"perf-repl-default-issuer"}
EOF
  vault_exec_service "$PRIMARY_SERVICE" \
    "VAULT_TOKEN=$root_token vault write ${TEST_MOUNT}/role/${TEST_ROLE} template=@/vault/runtime/templates/${TEST_ROLE}-template.json ttl=15m use_jti_claim=true" >/dev/null

  cat >"$APPROLE_PAYLOAD_FILE" <<EOF
{"token_type":"batch","token_policies":["${TEST_POLICY}"],"alias_metadata":{"spiffe_path":"${TEST_SPIFFE_PATH}","app":"${TEST_APPROLE}","experiment":"perf-repl-default-issuer"}}
EOF
  curl --silent --show-error --fail \
    --cacert "$ROOT_CA_FILE" \
    --header "X-Vault-Token: $root_token" \
    --header "Content-Type: application/json" \
    --request POST \
    --data @"$APPROLE_PAYLOAD_FILE" \
    "$PRIMARY_HOST_ADDR/v1/auth/approle/role/${TEST_APPROLE}" >/dev/null

  vault_exec_service "$PRIMARY_SERVICE" \
    "VAULT_TOKEN=$root_token vault read -field=role_id auth/approle/role/${TEST_APPROLE}/role-id" >"$RUNTIME_DIR/approle/${TEST_APPROLE}.role_id"
  vault_exec_service "$PRIMARY_SERVICE" \
    "VAULT_TOKEN=$root_token vault write -force -field=secret_id auth/approle/role/${TEST_APPROLE}/secret-id" >"$RUNTIME_DIR/approle/${TEST_APPROLE}.secret_id"
}

run_validation() {
  compose exec -T demo-tools bash -lc \
    "export PYTHONPATH=/workspace/demo/python; python /workspace/demo/scripts/internal/perf_replication_spiffe_issuer.py"
}

show_status() {
  show_command_output \
    "Primary performance replication status" \
    "curl --silent --show-error --cacert config/tls/hashibank-root-ca.crt ${PRIMARY_HOST_ADDR}/v1/sys/replication/performance/status | python3 -m json.tool"
  show_command_output \
    "Replica performance replication status" \
    "curl --silent --show-error --cacert config/tls/hashibank-root-ca.crt ${REPLICA_HOST_ADDR}/v1/sys/replication/performance/status | python3 -m json.tool"
  if [[ -f "$RESULT_FILE" ]]; then
    show_command_output \
      "Replica SPIFFE issuer validation result" \
      "cat runtime/generated/perf-repl-spiffe-issuer-result.json"
  else
    echo "Validation result not found yet. Run ./scripts/perf-repl-spiffe-issuer.sh first." >&2
    return 1
  fi
}

run_workflow() {
  ensure_primary_demo_bootstrap

  compose rm -sf "$REPLICA_SERVICE" >/dev/null 2>&1 || true
  rm -rf "$REPLICA_RUNTIME_DIR"
  rm -f \
    "$RESULT_FILE" \
    "$RUNTIME_DIR/generated/perf-repl-spiffe-issuer.jwt" \
    "$RUNTIME_DIR/approle/${TEST_APPROLE}.role_id" \
    "$RUNTIME_DIR/approle/${TEST_APPROLE}.secret_id"

  mkdir -p \
    "$REPLICA_RUNTIME_STORAGE_DIR" \
    "$RUNTIME_DIR/generated" \
    "$RUNTIME_DIR/approle" \
    "$RUNTIME_DIR/templates" \
    "$TLS_DIR"

  ensure_demo_tls_root_ca
  ensure_demo_server_cert "$REPLICA_SERVICE" "$REPLICA_SERVICE"

  echo "Starting performance replica Vault cluster..."
  compose up -d --build "$REPLICA_SERVICE" demo-tools >/dev/null 2>&1

  wait_for_https "$REPLICA_SERVICE" "$REPLICA_HOST_ADDR"

  initialise_and_unseal_vault_service "$REPLICA_SERVICE"

  enable_performance_primary
  enable_performance_secondary
  wait_for_replication_ready

  echo "Configuring replicated SPIFFE mount without jwt_issuer_url..."
  configure_test_mount

  echo "Validating issuer behavior from the performance replica..."
  run_validation

  cat <<EOF

Performance replica SPIFFE issuer experiment is ready.

Saved result:
  demo/runtime/generated/perf-repl-spiffe-issuer-result.json

Reprint the captured evidence:
  ./scripts/perf-repl-spiffe-issuer.sh status

Clean up the environment:
  ./scripts/teardown.sh
EOF
}

case "$ACTION" in
  run|status)
    ;;
  *)
    echo "Usage: ./scripts/perf-repl-spiffe-issuer.sh [status]" >&2
    exit 1
    ;;
esac

if [[ "$ACTION" == "run" ]]; then
  run_workflow
fi

show_status
