#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

APPROLE_DIR="$RUNTIME_DIR/approle"
TRUST_DIR="$RUNTIME_DIR/trust"
TEMPLATE_DIR="$RUNTIME_DIR/templates"

generate_root_ca() {
  if [[ -f "$TLS_DIR/hashibank-root-ca.crt" && -f "$TLS_DIR/hashibank-root-ca.key" ]]; then
    return 0
  fi

  echo "Generating HashiBank demo root CA..."
  openssl genrsa -out "$TLS_DIR/hashibank-root-ca.key" 4096 >/dev/null 2>&1
  openssl req -x509 -new -nodes \
    -key "$TLS_DIR/hashibank-root-ca.key" \
    -sha256 \
    -days 365 \
    -subj "/CN=HashiBank Demo Root CA" \
    -out "$TLS_DIR/hashibank-root-ca.crt" >/dev/null 2>&1
}

generate_server_cert() {
  local name="$1"
  local service_name="$2"
  local key_file="$TLS_DIR/$name.key"
  local csr_file="$TLS_DIR/$name.csr"
  local cert_file="$TLS_DIR/$name.crt"
  local ext_file="$TLS_DIR/$name.ext"

  if [[ -f "$cert_file" && -f "$key_file" ]]; then
    return 0
  fi

  cat >"$ext_file" <<EOF
subjectAltName=DNS:${service_name},DNS:localhost,IP:127.0.0.1
extendedKeyUsage=serverAuth
EOF

  openssl genrsa -out "$key_file" 2048 >/dev/null 2>&1
  openssl req -new -key "$key_file" -subj "/CN=${service_name}" -out "$csr_file" >/dev/null 2>&1
  openssl x509 -req \
    -in "$csr_file" \
    -CA "$TLS_DIR/hashibank-root-ca.crt" \
    -CAkey "$TLS_DIR/hashibank-root-ca.key" \
    -CAcreateserial \
    -out "$cert_file" \
    -days 365 \
    -sha256 \
    -extfile "$ext_file" >/dev/null 2>&1

  rm -f "$csr_file" "$ext_file"
}

extract_init_value() {
  local file="$1"
  local key="$2"
  awk -F': ' -v target="$key" '$1 == target {print $2}' "$file"
}

initialise_and_unseal() {
  local init_file="$VAULT_RUNTIME_DIR/init.txt"
  local initialized
  local sealed
  local unseal_key
  local root_token

  initialized=$(read_status_value "Initialized" || true)
  if [[ "$initialized" != "true" ]]; then
    echo "Initializing $VAULT_SERVICE..."
    vault_exec "vault operator init -key-shares=1 -key-threshold=1" >"$init_file"
  elif [[ ! -f "$init_file" ]]; then
    echo "Expected $init_file for already initialized $VAULT_SERVICE" >&2
    exit 1
  fi

  unseal_key=$(extract_init_value "$init_file" "Unseal Key 1")
  root_token=$(extract_init_value "$init_file" "Initial Root Token")
  printf '%s' "$root_token" >"$ROOT_TOKEN_FILE"

  sealed=$(read_status_value "Sealed" || true)
  if [[ "$sealed" == "true" ]]; then
    echo "Unsealing $VAULT_SERVICE..."
    vault_exec "vault operator unseal $unseal_key" >/dev/null
  fi
}

write_policies() {
  local root_token="$1"
  shift

  for policy in "$@"; do
    vault_exec "VAULT_TOKEN=$root_token vault policy write ${policy%.hcl} /vault/policies/${policy}.hcl" >/dev/null
  done
}

configure_vault() {
  local root_token
  local approle_accessor
  local fraud_template
  local assistant_template

  root_token=$(<"$ROOT_TOKEN_FILE")

  write_policies "$root_token" \
    identity-payments-issuer \
    identity-fraud-spiffe \
    identity-assistant-spiffe \
    access-payments \
    access-fraud

  if ! vault_exec "VAULT_TOKEN=$root_token vault auth list | grep -q '^approle/'"; then
    vault_exec "VAULT_TOKEN=$root_token vault auth enable approle" >/dev/null
  fi

  if ! vault_exec "VAULT_TOKEN=$root_token vault auth list | grep -q '^spiffe-x509/'"; then
    vault_exec "VAULT_TOKEN=$root_token vault auth enable -path=spiffe-x509 spiffe" >/dev/null
  fi

  if ! vault_exec "VAULT_TOKEN=$root_token vault auth list | grep -q '^spiffe-jwt/'"; then
    vault_exec "VAULT_TOKEN=$root_token vault auth enable -path=spiffe-jwt -passthrough-request-headers=Authorization spiffe" >/dev/null
  fi

  if ! vault_exec "VAULT_TOKEN=$root_token vault secrets list | grep -q '^pki/'"; then
    vault_exec "VAULT_TOKEN=$root_token vault secrets enable pki" >/dev/null
  fi

  if ! vault_exec "VAULT_TOKEN=$root_token vault secrets list | grep -q '^spiffe/'"; then
    vault_exec "VAULT_TOKEN=$root_token vault secrets enable spiffe" >/dev/null
  fi

  if ! vault_exec "VAULT_TOKEN=$root_token vault secrets list | grep -q '^kv/'"; then
    vault_exec "VAULT_TOKEN=$root_token vault secrets enable -path=kv kv-v2" >/dev/null
  fi

  if ! vault_exec "VAULT_TOKEN=$root_token vault secrets list | grep -q '^database/'"; then
    vault_exec "VAULT_TOKEN=$root_token vault secrets enable database" >/dev/null
  fi

  if ! vault_exec "VAULT_TOKEN=$root_token vault read pki/cert/ca >/dev/null 2>&1"; then
    vault_exec "VAULT_TOKEN=$root_token vault write pki/root/generate/internal common_name='HashiBank Demo SPIFFE Root' ttl=8760h" >/dev/null
  fi

  vault_exec "VAULT_TOKEN=$root_token vault read -field=certificate pki/cert/ca" >"$TRUST_DIR/hashibank-spiffe-root.pem"

  vault_exec "VAULT_TOKEN=$root_token vault write pki/roles/payments-spiffe allow_any_name=true enforce_hostnames=false require_cn=false allowed_uri_sans='spiffe://hashibank.demo/payments/*' max_ttl=1h" >/dev/null
  vault_exec "VAULT_TOKEN=$root_token vault write spiffe/config trust_domain=hashibank.demo jwt_issuer_url=https://hashibank-vault:8200/v1/spiffe jwt_oidc_compatibility_mode=true key_lifetime=24h bundle_refresh_hint=1h" >/dev/null
  vault_exec "VAULT_TOKEN=$root_token vault auth tune -passthrough-request-headers=Authorization spiffe-jwt/" >/dev/null
  vault_exec "VAULT_TOKEN=$root_token vault write auth/spiffe-x509/config trust_domain=hashibank.demo profile=static bundle=@/vault/runtime/trust/hashibank-spiffe-root.pem" >/dev/null
  vault_exec "VAULT_TOKEN=$root_token vault write auth/spiffe-jwt/config trust_domain=hashibank.demo profile=https_web_bundle endpoint_url=https://hashibank-vault:8200/v1/spiffe/trust_bundle/web endpoint_root_ca_truststore_pem=@/vault/config/tls/hashibank-root-ca.crt audience=hashibank-vault" >/dev/null

  curl --silent --show-error --fail \
    --cacert "$ROOT_CA_FILE" \
    --header "X-Vault-Token: $root_token" \
    --header "Content-Type: application/json" \
    --request POST \
    --data '{"token_type":"batch","token_policies":["identity-payments-issuer"],"alias_metadata":{"spiffe_path":"payments/api","app":"payments-api","line_of_business":"payments"}}' \
    "$VAULT_HOST_ADDR/v1/auth/approle/role/payments-api" >/dev/null

  curl --silent --show-error --fail \
    --cacert "$ROOT_CA_FILE" \
    --header "X-Vault-Token: $root_token" \
    --header "Content-Type: application/json" \
    --request POST \
    --data '{"token_type":"batch","token_policies":["identity-fraud-spiffe"],"alias_metadata":{"spiffe_path":"fraud/ops-web","app":"fraud-ops-web","line_of_business":"fraud"}}' \
    "$VAULT_HOST_ADDR/v1/auth/approle/role/fraud-ops-web" >/dev/null

  curl --silent --show-error --fail \
    --cacert "$ROOT_CA_FILE" \
    --header "X-Vault-Token: $root_token" \
    --header "Content-Type: application/json" \
    --request POST \
    --data '{"token_type":"batch","token_policies":["identity-assistant-spiffe"],"alias_metadata":{"spiffe_path":"ai/relationship-assistant","app":"relationship-assistant","line_of_business":"wealth"}}' \
    "$VAULT_HOST_ADDR/v1/auth/approle/role/relationship-assistant" >/dev/null

  approle_accessor=$(vault_exec "VAULT_TOKEN=$root_token vault auth list" | awk '$1 == "approle/" {print $3}')

  fraud_template="$TEMPLATE_DIR/fraud-ops-web-template.json"
  assistant_template="$TEMPLATE_DIR/relationship-assistant-template.json"

  cat >"$fraud_template" <<EOF
{"sub":"spiffe://hashibank.demo/{{identity.entity.aliases.${approle_accessor}.custom_metadata.spiffe_path}}","bank":"HashiBank"}
EOF

  cat >"$assistant_template" <<EOF
{"sub":"spiffe://hashibank.demo/{{identity.entity.aliases.${approle_accessor}.custom_metadata.spiffe_path}}","bank":"HashiBank"}
EOF

  vault_exec "VAULT_TOKEN=$root_token vault write spiffe/role/fraud-ops-web template=@/vault/runtime/templates/fraud-ops-web-template.json ttl=15m use_jti_claim=true" >/dev/null
  vault_exec "VAULT_TOKEN=$root_token vault write spiffe/role/relationship-assistant template=@/vault/runtime/templates/relationship-assistant-template.json ttl=15m use_jti_claim=true" >/dev/null

  vault_exec "VAULT_TOKEN=$root_token vault kv put kv/payments/api-secrets service=payments-api trust_domain=hashibank.demo message='Payments API KV secrets unlocked through SPIFFE X.509 auth'" >/dev/null
  vault_exec "VAULT_TOKEN=$root_token vault write database/config/hashibank-postgres plugin_name=postgresql-database-plugin allowed_roles=fraud-readonly connection_url='postgresql://{{username}}:{{password}}@postgres-hashibank:5432/hashibank?sslmode=disable' username=vaultadmin password=vaultadminpw password_authentication=scram-sha-256" >/dev/null
  vault_exec "VAULT_TOKEN=$root_token vault write database/roles/fraud-readonly db_name=hashibank-postgres creation_statements=\"CREATE ROLE \\\"{{name}}\\\" WITH LOGIN PASSWORD '{{password}}' VALID UNTIL '{{expiration}}'; GRANT CONNECT ON DATABASE hashibank TO \\\"{{name}}\\\"; GRANT USAGE ON SCHEMA public TO \\\"{{name}}\\\"; GRANT SELECT ON ALL TABLES IN SCHEMA public TO \\\"{{name}}\\\";\" default_ttl=1h max_ttl=24h" >/dev/null
  vault_exec "VAULT_TOKEN=$root_token vault write auth/spiffe-x509/role/payments-api display_name=payments-api token_type=batch token_policies=access-payments workload_id_patterns=payments/api" >/dev/null
  vault_exec "VAULT_TOKEN=$root_token vault write auth/spiffe-jwt/role/fraud-ops-web display_name=fraud-ops-web token_type=batch token_policies=access-fraud workload_id_patterns=fraud/ops-web" >/dev/null

  vault_exec "VAULT_TOKEN=$root_token vault read -field=role_id auth/approle/role/payments-api/role-id" >"$APPROLE_DIR/payments-api.role_id"
  vault_exec "VAULT_TOKEN=$root_token vault write -force -field=secret_id auth/approle/role/payments-api/secret-id" >"$APPROLE_DIR/payments-api.secret_id"
  vault_exec "VAULT_TOKEN=$root_token vault read -field=role_id auth/approle/role/fraud-ops-web/role-id" >"$APPROLE_DIR/fraud-ops-web.role_id"
  vault_exec "VAULT_TOKEN=$root_token vault write -force -field=secret_id auth/approle/role/fraud-ops-web/secret-id" >"$APPROLE_DIR/fraud-ops-web.secret_id"
  vault_exec "VAULT_TOKEN=$root_token vault read -field=role_id auth/approle/role/relationship-assistant/role-id" >"$APPROLE_DIR/relationship-assistant.role_id"
  vault_exec "VAULT_TOKEN=$root_token vault write -force -field=secret_id auth/approle/role/relationship-assistant/secret-id" >"$APPROLE_DIR/relationship-assistant.secret_id"
}

review_bootstrap() {
  if [[ ! -f "$ROOT_TOKEN_FILE" ]]; then
    echo "Bootstrap state not found. Run ./scripts/bootstrap.sh first." >&2
    exit 1
  fi

  printf '\nBootstrap review uses grouped Vault CLI output and pauses between sections.\n'

  show_heading "Group A: Policies"
  show_vault_command_output "Payments API issuer policy" "vault policy read identity-payments-issuer"
  show_vault_command_output "Fraud SPIFFE policy" "vault policy read identity-fraud-spiffe"
  show_vault_command_output "Assistant SPIFFE policy" "vault policy read identity-assistant-spiffe"
  show_vault_command_output "Payments access policy" "vault policy read access-payments"
  show_vault_command_output "Fraud access policy" "vault policy read access-fraud"
  pause_for_continue

  show_heading "Group B: AppRole definitions"
  show_vault_command_output "Payments AppRole definition" "vault read auth/approle/role/payments-api"
  show_vault_command_output "Fraud AppRole definition" "vault read auth/approle/role/fraud-ops-web"
  show_vault_command_output "Assistant AppRole definition" "vault read auth/approle/role/relationship-assistant"
  pause_for_continue

  show_heading "Group C: PKI role"
  show_vault_command_output "PKI role for payments certificates" "vault read pki/roles/payments-spiffe"
  pause_for_continue

  show_heading "Group D: SPIFFE engine config and SPIFFE roles"
  show_vault_command_output "SPIFFE engine configuration" "vault read spiffe/config"
  show_vault_command_output "Fraud SPIFFE role definition" "vault read spiffe/role/fraud-ops-web"
  show_vault_command_output "Assistant SPIFFE role definition" "vault read spiffe/role/relationship-assistant"
  pause_for_continue

  show_heading "Group E: SPIFFE auth configuration"
  show_vault_command_output "SPIFFE X.509 auth trust domain" "vault read -field=trust_domain auth/spiffe-x509/config"
  show_vault_command_output "SPIFFE X.509 auth profile" "vault read -field=profile auth/spiffe-x509/config"
  show_vault_command_output "SPIFFE JWT auth trust domain" "vault read -field=trust_domain auth/spiffe-jwt/config"
  show_vault_command_output "SPIFFE JWT auth profile" "vault read -field=profile auth/spiffe-jwt/config"
  show_vault_command_output "SPIFFE JWT auth audience" "vault read -field=audience auth/spiffe-jwt/config"
  show_vault_command_output "SPIFFE JWT auth endpoint" "vault read -field=endpoint_url auth/spiffe-jwt/config"
  pause_for_continue

  show_heading "Group F: SPIFFE auth roles"
  show_vault_command_output "SPIFFE X.509 auth role" "vault read auth/spiffe-x509/role/payments-api"
  show_vault_command_output "SPIFFE JWT auth role" "vault read auth/spiffe-jwt/role/fraud-ops-web"
  pause_for_continue

  show_heading "Group G: KV secrets"
  show_vault_command_output "Payments API KV secrets" "vault kv get kv/payments/api-secrets"
}

bootstrap_demo() {
  mkdir -p \
    "$VAULT_RUNTIME_DIR/file" \
    "$APPROLE_DIR" \
    "$TRUST_DIR" \
    "$TEMPLATE_DIR" \
    "$RUNTIME_DIR/generated" \
    "$RUNTIME_DIR/postgres" \
    "$TLS_DIR"

  generate_root_ca
  generate_server_cert "hashibank-vault" "hashibank-vault"

  echo "Starting HashiBank Vault Cluster and demo services..."
  compose up -d --build hashibank-vault postgres-hashibank demo-tools

  wait_for_https "$VAULT_SERVICE" "$VAULT_HOST_ADDR"
  wait_for_postgres

  initialise_and_unseal

  echo "Configuring HashiBank Vault Cluster..."
  configure_vault

  echo "Starting web experiences..."
  compose up -d --build hashibank-fraud-web hashibank-assistant

  cat <<EOF

HashiBank Vault SPIFFE demo is ready.

Bootstrap review:
  ./scripts/bootstrap.sh review

Payments API X.509 flow:
  ./scripts/demo-x509-payments.sh approle-login
  ./scripts/demo-x509-payments.sh pki-issue
  ./scripts/demo-x509-payments.sh spiffe-x509-auth
  ./scripts/demo-x509-payments.sh payments-api-kv-secrets
  ./scripts/demo-x509-payments.sh    # rerun full flow

Fraud Ops flow:
  ./scripts/demo-jwt-fraud.sh approle-login
  ./scripts/demo-jwt-fraud.sh mint-jwt
  ./scripts/demo-jwt-fraud.sh spiffe-jwt-auth
  ./scripts/demo-jwt-fraud.sh db-creds
  ./scripts/demo-jwt-fraud.sh final-reveal
  ./scripts/demo-jwt-fraud.sh         # rerun full flow
  http://localhost:${FRAUD_WEB_PORT}/

Relationship assistant flow:
  ./scripts/demo-agentic-oidc.sh approle-login
  ./scripts/demo-agentic-oidc.sh mint-jwt
  ./scripts/demo-agentic-oidc.sh fetch-discovery
  ./scripts/demo-agentic-oidc.sh validate-jwt
  ./scripts/demo-agentic-oidc.sh final-reveal
  ./scripts/demo-agentic-oidc.sh      # rerun full flow
  http://localhost:${ASSISTANT_WEB_PORT}/

To tear down and clean generated local artifacts:
  ./scripts/teardown.sh
EOF
}

case "${1:-up}" in
  review)
    review_bootstrap
    ;;
  up|bootstrap)
    bootstrap_demo
    ;;
  *)
    echo "Usage: ./scripts/bootstrap.sh [review]" >&2
    exit 1
    ;;
esac
