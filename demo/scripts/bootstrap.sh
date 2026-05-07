#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

IDENTITY_RUNTIME_DIR="$RUNTIME_DIR/hashibank-identity"
ACCESS_RUNTIME_DIR="$RUNTIME_DIR/hashibank-access"
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
  local service="$1"
  local runtime_dir="$2"
  local init_file="$runtime_dir/init.txt"
  local token_file="$runtime_dir/root-token"
  local initialized
  local sealed
  local unseal_key
  local root_token

  initialized=$(read_status_value "$service" "Initialized" || true)
  if [[ "$initialized" != "true" ]]; then
    echo "Initializing $service..."
    vault_exec "$service" "vault operator init -key-shares=1 -key-threshold=1" >"$init_file"
  elif [[ ! -f "$init_file" ]]; then
    echo "Expected $init_file for already initialized $service" >&2
    exit 1
  fi

  unseal_key=$(extract_init_value "$init_file" "Unseal Key 1")
  root_token=$(extract_init_value "$init_file" "Initial Root Token")
  printf '%s' "$root_token" >"$token_file"

  sealed=$(read_status_value "$service" "Sealed" || true)
  if [[ "$sealed" == "true" ]]; then
    echo "Unsealing $service..."
    vault_exec "$service" "vault operator unseal $unseal_key" >/dev/null
  fi
}

write_policies() {
  local service="$1"
  local root_token="$2"
  shift 2

  for policy in "$@"; do
    vault_exec "$service" "VAULT_TOKEN=$root_token vault policy write ${policy%.hcl} /vault/policies/${policy}.hcl" >/dev/null
  done
}

configure_identity() {
  local root_token
  local approle_accessor
  local fraud_template
  local assistant_template

  root_token=$(<"$IDENTITY_RUNTIME_DIR/root-token")

  write_policies hashibank-identity "$root_token" \
    identity-payments-issuer \
    identity-fraud-spiffe \
    identity-assistant-spiffe

  if ! vault_exec hashibank-identity "VAULT_TOKEN=$root_token vault auth list | grep -q '^approle/'"; then
    vault_exec hashibank-identity "VAULT_TOKEN=$root_token vault auth enable approle" >/dev/null
  fi

  if ! vault_exec hashibank-identity "VAULT_TOKEN=$root_token vault secrets list | grep -q '^pki/'"; then
    vault_exec hashibank-identity "VAULT_TOKEN=$root_token vault secrets enable pki" >/dev/null
  fi

  if ! vault_exec hashibank-identity "VAULT_TOKEN=$root_token vault secrets list | grep -q '^spiffe/'"; then
    vault_exec hashibank-identity "VAULT_TOKEN=$root_token vault secrets enable spiffe" >/dev/null
  fi

  if ! vault_exec hashibank-identity "VAULT_TOKEN=$root_token vault read pki/cert/ca >/dev/null 2>&1"; then
    vault_exec hashibank-identity "VAULT_TOKEN=$root_token vault write pki/root/generate/internal common_name='HashiBank Demo SPIFFE Root' ttl=8760h" >/dev/null
  fi

  vault_exec hashibank-identity "VAULT_TOKEN=$root_token vault read -field=certificate pki/cert/ca" >"$TRUST_DIR/hashibank-spiffe-root.pem"

  vault_exec hashibank-identity "VAULT_TOKEN=$root_token vault write pki/roles/payments-spiffe allow_any_name=true enforce_hostnames=false require_cn=false allowed_uri_sans='spiffe://hashibank.demo/payments/*' max_ttl=1h" >/dev/null

  vault_exec hashibank-identity "VAULT_TOKEN=$root_token vault write spiffe/config trust_domain=hashibank.demo jwt_issuer_url=https://hashibank-identity:8200/v1/spiffe jwt_oidc_compatibility_mode=true key_lifetime=24h bundle_refresh_hint=1h" >/dev/null

  curl --silent --show-error --fail \
    --cacert "$ROOT_CA_FILE" \
    --header "X-Vault-Token: $root_token" \
    --header "Content-Type: application/json" \
    --request POST \
    --data '{"token_type":"batch","token_policies":["identity-payments-issuer"],"alias_metadata":{"spiffe_path":"payments/api","app":"payments-api","line_of_business":"payments"}}' \
    "$IDENTITY_HOST_ADDR/v1/auth/approle/role/payments-api" >/dev/null

  curl --silent --show-error --fail \
    --cacert "$ROOT_CA_FILE" \
    --header "X-Vault-Token: $root_token" \
    --header "Content-Type: application/json" \
    --request POST \
    --data '{"token_type":"batch","token_policies":["identity-fraud-spiffe"],"alias_metadata":{"spiffe_path":"fraud/ops-web","app":"fraud-ops-web","line_of_business":"fraud"}}' \
    "$IDENTITY_HOST_ADDR/v1/auth/approle/role/fraud-ops-web" >/dev/null

  curl --silent --show-error --fail \
    --cacert "$ROOT_CA_FILE" \
    --header "X-Vault-Token: $root_token" \
    --header "Content-Type: application/json" \
    --request POST \
    --data '{"token_type":"batch","token_policies":["identity-assistant-spiffe"],"alias_metadata":{"spiffe_path":"ai/relationship-assistant","app":"relationship-assistant","line_of_business":"wealth"}}' \
    "$IDENTITY_HOST_ADDR/v1/auth/approle/role/relationship-assistant" >/dev/null

  approle_accessor=$(vault_exec hashibank-identity "VAULT_TOKEN=$root_token vault auth list" | awk '$1 == "approle/" {print $3}')

  fraud_template="$TEMPLATE_DIR/fraud-ops-web-template.json"
  assistant_template="$TEMPLATE_DIR/relationship-assistant-template.json"

  cat >"$fraud_template" <<EOF
{"sub":"spiffe://hashibank.demo/{{identity.entity.aliases.${approle_accessor}.custom_metadata.spiffe_path}}","bank":"HashiBank"}
EOF

  cat >"$assistant_template" <<EOF
{"sub":"spiffe://hashibank.demo/{{identity.entity.aliases.${approle_accessor}.custom_metadata.spiffe_path}}","bank":"HashiBank"}
EOF

  vault_exec hashibank-identity "VAULT_TOKEN=$root_token vault write spiffe/role/fraud-ops-web template=@/vault/runtime/templates/fraud-ops-web-template.json ttl=15m use_jti_claim=true" >/dev/null
  vault_exec hashibank-identity "VAULT_TOKEN=$root_token vault write spiffe/role/relationship-assistant template=@/vault/runtime/templates/relationship-assistant-template.json ttl=15m use_jti_claim=true" >/dev/null

  vault_exec hashibank-identity "VAULT_TOKEN=$root_token vault read -field=role_id auth/approle/role/payments-api/role-id" >"$APPROLE_DIR/payments-api.role_id"
  vault_exec hashibank-identity "VAULT_TOKEN=$root_token vault write -force -field=secret_id auth/approle/role/payments-api/secret-id" >"$APPROLE_DIR/payments-api.secret_id"

  vault_exec hashibank-identity "VAULT_TOKEN=$root_token vault read -field=role_id auth/approle/role/fraud-ops-web/role-id" >"$APPROLE_DIR/fraud-ops-web.role_id"
  vault_exec hashibank-identity "VAULT_TOKEN=$root_token vault write -force -field=secret_id auth/approle/role/fraud-ops-web/secret-id" >"$APPROLE_DIR/fraud-ops-web.secret_id"

  vault_exec hashibank-identity "VAULT_TOKEN=$root_token vault read -field=role_id auth/approle/role/relationship-assistant/role-id" >"$APPROLE_DIR/relationship-assistant.role_id"
  vault_exec hashibank-identity "VAULT_TOKEN=$root_token vault write -force -field=secret_id auth/approle/role/relationship-assistant/secret-id" >"$APPROLE_DIR/relationship-assistant.secret_id"
}

configure_access() {
  local root_token

  root_token=$(<"$ACCESS_RUNTIME_DIR/root-token")

  write_policies hashibank-access "$root_token" access-payments access-fraud

  if ! vault_exec hashibank-access "VAULT_TOKEN=$root_token vault secrets list | grep -q '^kv/'"; then
    vault_exec hashibank-access "VAULT_TOKEN=$root_token vault secrets enable -path=kv kv-v2" >/dev/null
  fi

  if ! vault_exec hashibank-access "VAULT_TOKEN=$root_token vault secrets list | grep -q '^database/'"; then
    vault_exec hashibank-access "VAULT_TOKEN=$root_token vault secrets enable database" >/dev/null
  fi

  vault_exec hashibank-access "VAULT_TOKEN=$root_token vault kv put kv/payments/bootstrap service=payments-api trust_domain=hashibank.demo message='Payments proof path unlocked through SPIFFE X.509 auth'" >/dev/null

  vault_exec hashibank-access "VAULT_TOKEN=$root_token vault write database/config/hashibank-postgres plugin_name=postgresql-database-plugin allowed_roles=fraud-readonly connection_url='postgresql://{{username}}:{{password}}@postgres-hashibank:5432/hashibank?sslmode=disable' username=vaultadmin password=vaultadminpw password_authentication=scram-sha-256" >/dev/null
  vault_exec hashibank-access "VAULT_TOKEN=$root_token vault write database/roles/fraud-readonly db_name=hashibank-postgres creation_statements=\"CREATE ROLE \\\"{{name}}\\\" WITH LOGIN PASSWORD '{{password}}' VALID UNTIL '{{expiration}}'; GRANT CONNECT ON DATABASE hashibank TO \\\"{{name}}\\\"; GRANT USAGE ON SCHEMA public TO \\\"{{name}}\\\"; GRANT SELECT ON ALL TABLES IN SCHEMA public TO \\\"{{name}}\\\";\" default_ttl=1h max_ttl=24h" >/dev/null

  if ! vault_exec hashibank-access "VAULT_TOKEN=$root_token vault auth list | grep -q '^spiffe-x509/'"; then
    vault_exec hashibank-access "VAULT_TOKEN=$root_token vault auth enable -path=spiffe-x509 spiffe" >/dev/null
  fi

  if ! vault_exec hashibank-access "VAULT_TOKEN=$root_token vault auth list | grep -q '^spiffe-jwt/'"; then
    vault_exec hashibank-access "VAULT_TOKEN=$root_token vault auth enable -path=spiffe-jwt -passthrough-request-headers=Authorization spiffe" >/dev/null
  fi

  vault_exec hashibank-access "VAULT_TOKEN=$root_token vault auth tune -passthrough-request-headers=Authorization spiffe-jwt/" >/dev/null

  vault_exec hashibank-access "VAULT_TOKEN=$root_token vault write auth/spiffe-x509/config trust_domain=hashibank.demo profile=static bundle=@/vault/runtime/trust/hashibank-spiffe-root.pem" >/dev/null
  vault_exec hashibank-access "VAULT_TOKEN=$root_token vault write auth/spiffe-jwt/config trust_domain=hashibank.demo profile=https_web_bundle endpoint_url=https://hashibank-identity:8200/v1/spiffe/trust_bundle/web endpoint_root_ca_truststore_pem=@/vault/config/tls/hashibank-root-ca.crt audience=hashibank-access" >/dev/null

  vault_exec hashibank-access "VAULT_TOKEN=$root_token vault write auth/spiffe-x509/role/payments-api display_name=payments-api token_type=batch token_policies=access-payments workload_id_patterns=payments/api" >/dev/null
  vault_exec hashibank-access "VAULT_TOKEN=$root_token vault write auth/spiffe-jwt/role/fraud-ops-web display_name=fraud-ops-web token_type=batch token_policies=access-fraud workload_id_patterns=fraud/ops-web" >/dev/null
}

main() {
  mkdir -p \
    "$IDENTITY_RUNTIME_DIR/file" \
    "$ACCESS_RUNTIME_DIR/file" \
    "$APPROLE_DIR" \
    "$TRUST_DIR" \
    "$TEMPLATE_DIR" \
    "$RUNTIME_DIR/generated" \
    "$TLS_DIR" \
    "$RUNTIME_DIR/postgres"

  generate_root_ca
  generate_server_cert "hashibank-identity" "hashibank-identity"
  generate_server_cert "hashibank-access" "hashibank-access"

  echo "Starting core demo services..."
  compose up -d --build hashibank-identity hashibank-access postgres-hashibank demo-tools

  wait_for_https "hashibank-identity" "$IDENTITY_HOST_ADDR"
  wait_for_https "hashibank-access" "$ACCESS_HOST_ADDR"
  wait_for_postgres

  initialise_and_unseal "hashibank-identity" "$IDENTITY_RUNTIME_DIR"
  initialise_and_unseal "hashibank-access" "$ACCESS_RUNTIME_DIR"

  echo "Configuring hashibank-identity..."
  configure_identity

  echo "Configuring hashibank-access..."
  configure_access

  echo "Starting web experiences..."
  compose up -d --build hashibank-fraud-web hashibank-assistant

  cat <<EOF

HashiBank Vault SPIFFE demo is ready.

Payments API X.509 flow:
  ./scripts/demo-x509-payments.sh

Fraud Ops flow:
  ./scripts/demo-jwt-fraud.sh
  http://localhost:${FRAUD_WEB_PORT}/

Relationship assistant flow:
  ./scripts/demo-agentic-oidc.sh
  http://localhost:${ASSISTANT_WEB_PORT}/

To tear down and clean generated local artifacts:
  ./scripts/teardown.sh
EOF
}

main "$@"
