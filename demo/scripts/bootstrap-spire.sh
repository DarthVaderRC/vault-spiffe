#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

ACTION="${1:-run}"

SPIRE_TRUST_DOMAIN="spire.hashibank.demo"
SPIRE_BUNDLE_ENDPOINT_URL="https://spire-server:8443"
SPIRE_UPSTREAM_POLICY="spire-upstreamauthority"
SPIRE_UPSTREAM_APPROLE="spire-upstreamauthority"
SPIRE_UPSTREAM_ROLE_ID_FILE="$SPIRE_SERVER_RUNTIME_DIR/approle.role_id"
SPIRE_UPSTREAM_SECRET_ID_FILE="$SPIRE_SERVER_RUNTIME_DIR/approle.secret_id"
SPIRE_UPSTREAM_BOOTSTRAP_BUNDLE_FILE="$SPIRE_AGENT_BOOTSTRAP_BUNDLE_FILE"
SPIRE_WORKLOAD_NAME="vault-spire-client"
SPIRE_WORKLOAD_ID_PATH="workloads/vault-spire-client"
SPIRE_WORKLOAD_SPIFFE_ID="spiffe://${SPIRE_TRUST_DOMAIN}/${SPIRE_WORKLOAD_ID_PATH}"
SPIRE_WORKLOAD_SELECTOR="docker:label:org.hashibank.spire-role:vault-spire-client"
SPIRE_WORKLOAD_PARENT_ID_FILE="$SPIRE_AGENT_RUNTIME_DIR/parent_id"
SPIRE_JOIN_TOKEN_FILE="$SPIRE_AGENT_RUNTIME_DIR/join.token"
SPIRE_JWT_AUDIENCE="vault-spire-demo"
SPIRE_KV_MESSAGE="Vault read unlocked by a SPIRE-issued SVID"
SPIRE_JWT_AUTH_PATH="spire-jwt"
SPIRE_VAULT_ROLE="vault-spire-client"

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

render_spire_agent_config() {
  local join_token="$1"

  sed "s#__JOIN_TOKEN__#${join_token}#g" "$DEMO_DIR/config/spire/agent.conf.tpl" >"$SPIRE_AGENT_CONFIG_FILE"
}

configure_vault_for_spire_upstream() {
  local root_token

  root_token=$(<"$ROOT_TOKEN_FILE")

  write_policies "$root_token" access-spire-demo "$SPIRE_UPSTREAM_POLICY"

  if ! vault_exec "VAULT_TOKEN=$root_token vault secrets list | grep -q '^spire-pki/'"; then
    vault_exec "VAULT_TOKEN=$root_token vault secrets enable -path=spire-pki pki" >/dev/null
  fi

  vault_exec "VAULT_TOKEN=$root_token vault secrets tune -max-lease-ttl=8760h spire-pki" >/dev/null
  if ! vault_exec "VAULT_TOKEN=$root_token vault read spire-pki/cert/ca >/dev/null 2>&1"; then
    vault_exec "VAULT_TOKEN=$root_token vault write spire-pki/root/generate/internal common_name='HashiBank SPIRE Upstream Root' ttl=8760h" >/dev/null
  fi
  vault_exec "VAULT_TOKEN=$root_token vault read -field=certificate spire-pki/cert/ca" >"$SPIRE_UPSTREAM_BOOTSTRAP_BUNDLE_FILE"

  if ! vault_exec "VAULT_TOKEN=$root_token vault auth list | grep -q '^${SPIRE_JWT_AUTH_PATH}/'"; then
    vault_exec "VAULT_TOKEN=$root_token vault auth enable -path=${SPIRE_JWT_AUTH_PATH} -passthrough-request-headers=Authorization spiffe" >/dev/null
  else
    vault_exec "VAULT_TOKEN=$root_token vault auth tune -passthrough-request-headers=Authorization ${SPIRE_JWT_AUTH_PATH}/" >/dev/null
  fi
  vault_exec "VAULT_TOKEN=$root_token vault write auth/approle/role/${SPIRE_UPSTREAM_APPROLE} token_type=service token_ttl=24h token_max_ttl=24h token_policies=${SPIRE_UPSTREAM_POLICY}" >/dev/null
  vault_exec "VAULT_TOKEN=$root_token vault read -field=role_id auth/approle/role/${SPIRE_UPSTREAM_APPROLE}/role-id" >"$SPIRE_UPSTREAM_ROLE_ID_FILE"
  vault_exec "VAULT_TOKEN=$root_token vault write -force -field=secret_id auth/approle/role/${SPIRE_UPSTREAM_APPROLE}/secret-id" >"$SPIRE_UPSTREAM_SECRET_ID_FILE"
}

configure_vault_spire_auth() {
  local root_token

  root_token=$(<"$ROOT_TOKEN_FILE")

  vault_exec "VAULT_TOKEN=$root_token vault write auth/${SPIRE_JWT_AUTH_PATH}/config trust_domain=${SPIRE_TRUST_DOMAIN} profile=https_web_bundle endpoint_url=${SPIRE_BUNDLE_ENDPOINT_URL} endpoint_root_ca_truststore_pem=@/vault/config/tls/hashibank-root-ca.crt audience=${SPIRE_JWT_AUDIENCE}" >/dev/null

  vault_exec "VAULT_TOKEN=$root_token vault write auth/${SPIRE_JWT_AUTH_PATH}/role/${SPIRE_VAULT_ROLE} display_name=${SPIRE_VAULT_ROLE} token_type=batch token_policies=access-spire-demo workload_id_patterns=${SPIRE_WORKLOAD_ID_PATH}" >/dev/null

  vault_exec "VAULT_TOKEN=$root_token vault kv put kv/spire/demo trust_domain=${SPIRE_TRUST_DOMAIN} message='${SPIRE_KV_MESSAGE}'" >/dev/null
}

generate_join_token() {
  local token_json
  local join_token
  local parent_id

  token_json=$(spire_server_exec token generate -socketPath "$SPIRE_SERVER_SOCKET_PATH" -output json)
  join_token=$(printf '%s' "$token_json" | json_get "token")
  if [[ -z "$join_token" ]]; then
    join_token=$(printf '%s' "$token_json" | json_get "value")
  fi
  if [[ -z "$join_token" ]]; then
    echo "Failed to read SPIRE join token from server output" >&2
    printf '%s\n' "$token_json" >&2
    return 1
  fi

  parent_id="spiffe://${SPIRE_TRUST_DOMAIN}/spire/agent/join_token/${join_token}"
  printf '%s' "$join_token" >"$SPIRE_JOIN_TOKEN_FILE"
  printf '%s' "$parent_id" >"$SPIRE_WORKLOAD_PARENT_ID_FILE"
  render_spire_agent_config "$join_token"
}

create_spire_entries() {
  local parent_id

  parent_id=$(<"$SPIRE_WORKLOAD_PARENT_ID_FILE")
  spire_server_exec entry create \
    -socketPath "$SPIRE_SERVER_SOCKET_PATH" \
    -parentID "$parent_id" \
    -spiffeID "$SPIRE_WORKLOAD_SPIFFE_ID" \
    -selector "$SPIRE_WORKLOAD_SELECTOR" \
    -x509SVIDTTL 900 \
    -jwtSVIDTTL 900 >/dev/null
}

wait_for_workload_identity() {
  for _ in $(seq 1 60); do
    if spire_client_exec "spire-agent api fetch x509 -socketPath ${SPIRE_AGENT_SOCKET_PATH} -output json >/dev/null" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done

  echo "Timed out waiting for the SPIRE workload identity" >&2
  return 1
}

show_status() {
  show_command_output \
    "SPIRE server bundle" \
    "spire-server bundle show -socketPath ${SPIRE_SERVER_SOCKET_PATH} -output json" \
    "docker compose -f docker-compose.yml exec -T ${SPIRE_SERVER_SERVICE} /opt/spire/bin/spire-server bundle show -socketPath ${SPIRE_SERVER_SOCKET_PATH} -output json"
  show_command_output \
    "SPIRE agent health" \
    "spire-agent healthcheck -socketPath ${SPIRE_AGENT_SOCKET_PATH}" \
    "docker compose -f docker-compose.yml exec -T ${SPIRE_AGENT_SERVICE} /opt/spire/bin/spire-agent healthcheck -socketPath ${SPIRE_AGENT_SOCKET_PATH}"
  show_command_output \
    "Vault SPIRE JWT auth config" \
    "VAULT_ADDR=${VAULT_HOST_ADDR} VAULT_CACERT=config/tls/hashibank-root-ca.crt VAULT_TOKEN=\$(cat runtime/hashibank-vault/root-token) vault read auth/${SPIRE_JWT_AUTH_PATH}/config"
}

run_workflow() {
  ensure_hashibank_demo_bootstrap

  compose rm -sf "$SPIRE_SERVER_SERVICE" "$SPIRE_AGENT_SERVICE" "$SPIRE_CLIENT_SERVICE" >/dev/null 2>&1 || true
  rm -rf "$SPIRE_RUNTIME_DIR"

  mkdir -p \
    "$SPIRE_SERVER_DATA_DIR" \
    "$SPIRE_SERVER_SOCKET_DIR" \
    "$SPIRE_AGENT_DATA_DIR" \
    "$SPIRE_AGENT_BOOTSTRAP_DIR" \
    "$SPIRE_AGENT_SOCKET_DIR"

  ensure_demo_tls_root_ca
  ensure_demo_server_cert "$SPIRE_SERVER_SERVICE" "$SPIRE_SERVER_SERVICE"

  echo "Configuring Vault for SPIRE integration..."
  configure_vault_for_spire_upstream

  echo "Starting SPIRE server..."
  SPIRE_VAULT_APPROLE_ID=$(<"$SPIRE_UPSTREAM_ROLE_ID_FILE") \
  SPIRE_VAULT_APPROLE_SECRET_ID=$(<"$SPIRE_UPSTREAM_SECRET_ID_FILE") \
    compose up -d "$SPIRE_SERVER_SERVICE" >/dev/null 2>&1
  wait_for_spire_server_api
  wait_for_spire_bundle_endpoint

  echo "Configuring Vault SPIRE auth mounts..."
  configure_vault_spire_auth

  echo "Creating SPIRE agent join token..."
  generate_join_token
  create_spire_entries

  echo "Starting SPIRE agent and workload client..."
  compose up -d --build "$SPIRE_AGENT_SERVICE" "$SPIRE_CLIENT_SERVICE" demo-tools >/dev/null 2>&1
  wait_for_spire_agent_api
  wait_for_workload_identity

  cat <<EOF

SPIRE overlay is ready.

Bundle endpoint:
  ${SPIRE_BUNDLE_ENDPOINT_HOST_ADDR}

Supported demos:
  ./scripts/demo-spire-jwt.sh
  ./scripts/demo-spire-upstreamauthority.sh

Known limitation:
  SPIRE X.509-SVID -> Vault SPIFFE auth is not enabled here because auth still
  failed when Vault trusted the SPIRE federation bundle/root.
  The same workload authenticated only when Vault trusted the SPIRE issuing
  intermediate directly. That workaround is intentionally omitted because it
  diverges from the intended "fetch trust bundle from SPIRE" model.

Bootstrap script can be rerun safely:
  ./scripts/bootstrap-spire.sh
EOF
}

case "$ACTION" in
  run|status)
    ;;
  *)
    echo "Usage: ./scripts/bootstrap-spire.sh [status]" >&2
    exit 1
    ;;
esac

if [[ "$ACTION" == "run" ]]; then
  run_workflow
fi

show_status
