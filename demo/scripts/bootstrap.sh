#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

ACTION="${1:-up}"

TRUST_DIR="$RUNTIME_DIR/trust"
TEMPLATE_DIR="$RUNTIME_DIR/templates"
KIND_CONFIG_FILE="$DEMO_DIR/kind/kind-config.yaml"
KIND_MANIFESTS_DIR="$DEMO_DIR/kind/manifests"
KIND_CA_FILE="$KIND_RUNTIME_DIR/kubernetes-ca.crt"
REVIEWER_TOKEN_FILE="$KIND_RUNTIME_DIR/reviewer-token.jwt"
TOOLS_IMAGE="vault-spiffe-demo-tools:local"
MTLS_IMAGE="vault-spiffe-mtls-backend:local"
JWT_IMAGE="vault-spiffe-jwt-consumer:local"

SPIRE_TRUST_DOMAIN="spire.hashibank.demo"
SPIRE_BUNDLE_ENDPOINT_URL="https://spire-server:8443"
SPIRE_UPSTREAM_POLICY="spire-upstreamauthority"
SPIRE_UPSTREAM_TOKEN_FILE="$SPIRE_SERVER_RUNTIME_DIR/vault.token"
SPIRE_UPSTREAM_BOOTSTRAP_BUNDLE_FILE="$SPIRE_AGENT_BOOTSTRAP_BUNDLE_FILE"
SPIRE_WORKLOAD_NAME="vault-spire-client"
SPIRE_WORKLOAD_ID_PATH="workloads/vault-spire-client"
SPIRE_WORKLOAD_SPIFFE_ID="spiffe://${SPIRE_TRUST_DOMAIN}/${SPIRE_WORKLOAD_ID_PATH}"
SPIRE_WORKLOAD_SELECTOR="docker:label:org.hashibank.spire-role:vault-spire-client"
SPIRE_WORKLOAD_PARENT_ID_FILE="$SPIRE_AGENT_RUNTIME_DIR/parent_id"
SPIRE_JOIN_TOKEN_FILE="$SPIRE_AGENT_RUNTIME_DIR/join.token"
SPIRE_JWT_AUDIENCE="vault-spire-demo"
SPIRE_JWT_AUTH_PATH="spire-jwt"
SPIRE_VAULT_ROLE="vault-spire-client"
SPIRE_DB_CONFIG_NAME="hashibank-postgres"
SPIRE_DB_ROLE="fraud-readonly"
SPIRE_DB_CONNECTION_URL="postgresql://{{username}}:{{password}}@postgres-hashibank:5432/hashibank?sslmode=disable"
SPIRE_DB_CREATION_FILE="$RUNTIME_DIR/generated/spire-fraud-readonly.sql"
SPIRE_DB_REVOCATION_FILE="$RUNTIME_DIR/generated/spire-fraud-readonly-revoke.sql"

ASSISTANT_DB_CONFIG_NAME="hashibank-insights-db"
ASSISTANT_DB_ROLE="assistant-insights-readonly"
ASSISTANT_DB_CONNECTION_URL="postgresql://{{username}}:{{password}}@postgres-hashibank:5432/hashibank?sslmode=disable"
ASSISTANT_DB_CREATION_FILE="$RUNTIME_DIR/generated/assistant-insights-readonly.sql"
ASSISTANT_DB_REVOCATION_FILE="$RUNTIME_DIR/generated/assistant-insights-readonly-revoke.sql"

kubectl_host() {
  KUBECONFIG="$KUBECONFIG_HOST_FILE" kubectl "$@"
}

kind_cluster_exists() {
  kind get clusters 2>/dev/null | grep -Fxq "$KIND_CLUSTER_NAME"
}

require_local_k8s_tooling() {
  local missing=()
  local command

  for command in docker kubectl kind; do
    if ! command_exists "$command"; then
      missing+=("$command")
    fi
  done

  if ((${#missing[@]})); then
    echo "Missing required tooling for ./scripts/bootstrap.sh: ${missing[*]}" >&2
    echo "The unified bootstrap provisions the Vault cluster and the kind-based Kubernetes overlay together." >&2
    exit 1
  fi
}

ensure_bootstrap_directories() {
  mkdir -p \
    "$VAULT_RUNTIME_STORAGE_DIR" \
    "$KIND_RUNTIME_DIR" \
    "$TRUST_DIR" \
    "$TEMPLATE_DIR" \
    "$RUNTIME_DIR/generated" \
    "$TLS_DIR"

  rm -rf "$RUNTIME_DIR/approle"
  rm -f \
    "$TEMPLATE_DIR/fraud-ops-web-template.json" \
    "$TEMPLATE_DIR/relationship-assistant-template.json"
}

disable_vault_auth_mount_if_present() {
  local root_token="$1"
  local mount="$2"

  if vault_exec "VAULT_TOKEN=$root_token vault auth list | grep -q '^${mount}/'"; then
    vault_exec "VAULT_TOKEN=$root_token vault auth disable ${mount}" >/dev/null
  fi
}

disable_vault_secrets_mount_if_present() {
  local root_token="$1"
  local mount="$2"

  if vault_exec "VAULT_TOKEN=$root_token vault secrets list | grep -q '^${mount}/'"; then
    vault_exec "VAULT_TOKEN=$root_token vault secrets disable ${mount}" >/dev/null
  fi
}

configure_vault_base() {
  local root_token

  root_token=$(<"$ROOT_TOKEN_FILE")

  disable_vault_auth_mount_if_present "$root_token" "approle"
  disable_vault_auth_mount_if_present "$root_token" "spiffe-x509"
  disable_vault_auth_mount_if_present "$root_token" "spiffe-jwt"
  disable_vault_secrets_mount_if_present "$root_token" "database"

  if ! vault_exec "VAULT_TOKEN=$root_token vault secrets list | grep -q '^pki/'"; then
    vault_exec "VAULT_TOKEN=$root_token vault secrets enable pki" >/dev/null
  fi

  if ! vault_exec "VAULT_TOKEN=$root_token vault secrets list | grep -q '^spiffe/'"; then
    vault_exec "VAULT_TOKEN=$root_token vault secrets enable spiffe" >/dev/null
  fi

  if ! vault_exec "VAULT_TOKEN=$root_token vault secrets list | grep -q '^kv/'"; then
    vault_exec "VAULT_TOKEN=$root_token vault secrets enable -path=kv kv-v2" >/dev/null
  fi

  vault_exec "VAULT_TOKEN=$root_token vault write pki/config/urls issuing_certificates='${VAULT_PUBLIC_ADDR}/v1/pki/ca' crl_distribution_points='${VAULT_PUBLIC_ADDR}/v1/pki/crl'" >/dev/null

  if ! vault_exec "VAULT_TOKEN=$root_token vault read pki/cert/ca >/dev/null 2>&1"; then
    vault_exec "VAULT_TOKEN=$root_token vault write pki/root/generate/internal common_name='HashiBank Demo SPIFFE Root' ttl=8760h" >/dev/null
  fi

  vault_exec "VAULT_TOKEN=$root_token vault read -field=certificate pki/cert/ca" >"$TRUST_DIR/hashibank-spiffe-root.pem"
  vault_exec "VAULT_TOKEN=$root_token vault write spiffe/config trust_domain=hashibank.demo jwt_issuer_url=${VAULT_PUBLIC_ADDR}/v1/spiffe jwt_oidc_compatibility_mode=true key_lifetime=24h bundle_refresh_hint=1h" >/dev/null
}

ensure_vault_service() {
  ensure_demo_tls_root_ca
  ensure_demo_server_cert \
    "hashibank-vault" \
    "hashibank-vault" \
    "DNS:localhost" \
    "IP:127.0.0.1" \
    "DNS:${VAULT_PUBLIC_HOSTNAME}" \
    "DNS:host.docker.internal"

  echo "Starting HashiBank Vault Cluster and demo tools..."
  compose up -d --build --force-recreate "$VAULT_SERVICE" demo-tools >/dev/null 2>&1
  wait_for_vault_service "$VAULT_SERVICE"
  initialise_and_unseal_vault_service "$VAULT_SERVICE"

  echo "Configuring HashiBank Vault base services..."
  configure_vault_base
}

ensure_kind_cluster() {
  mkdir -p "$KIND_RUNTIME_DIR"

  if kind_cluster_exists; then
    return 0
  fi

  echo "Creating kind cluster ${KIND_CLUSTER_NAME}..."
  kind create cluster --name "$KIND_CLUSTER_NAME" --config "$KIND_CONFIG_FILE" >/dev/null
}

write_kubeconfigs() {
  kind get kubeconfig --name "$KIND_CLUSTER_NAME" >"$KUBECONFIG_HOST_FILE"
  sed "s#https://127.0.0.1:${KIND_API_PORT}#https://host.docker.internal:${KIND_API_PORT}#g" \
    "$KUBECONFIG_HOST_FILE" >"$KUBECONFIG_DOCKER_FILE"
  KUBECONFIG="$KUBECONFIG_HOST_FILE" kubectl config view --raw --minify -o jsonpath='{.clusters[0].cluster.certificate-authority-data}' \
    | base64 --decode >"$KIND_CA_FILE"
}

wait_for_kind_cluster() {
  kubectl_host wait --for=condition=Ready node --all --timeout=180s >/dev/null
}

build_and_load_kind_images() {
  echo "Building local images for kind workloads..."
  (
    cd "$DEMO_DIR"
    docker build -t "$TOOLS_IMAGE" -f tools/Dockerfile .
    docker build -t "$MTLS_IMAGE" -f apps/mtls-backend/Dockerfile .
    docker build -t "$JWT_IMAGE" -f apps/jwt-consumer/Dockerfile .
  ) >/dev/null

  kind load docker-image --name "$KIND_CLUSTER_NAME" "$TOOLS_IMAGE" >/dev/null
  kind load docker-image --name "$KIND_CLUSTER_NAME" "$MTLS_IMAGE" >/dev/null
  kind load docker-image --name "$KIND_CLUSTER_NAME" "$JWT_IMAGE" >/dev/null
}

apply_namespaces_and_rbac() {
  kubectl_host apply -f "$KIND_MANIFESTS_DIR/namespaces.yaml" >/dev/null
  kubectl_host apply -f "$KIND_MANIFESTS_DIR/reviewer-rbac.yaml" >/dev/null
}

mint_reviewer_token() {
  local token

  token=$(kubectl_host -n "$K8S_VAULT_NAMESPACE" create token "$K8S_REVIEWER_SERVICE_ACCOUNT" --duration=168h 2>/dev/null || true)
  if [[ -n "$token" ]]; then
    printf '%s' "$token" >"$REVIEWER_TOKEN_FILE"
    return 0
  fi

  kubectl_host -n "$K8S_VAULT_NAMESPACE" apply -f - >/dev/null <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: vault-auth-reviewer-token
  annotations:
    kubernetes.io/service-account.name: ${K8S_REVIEWER_SERVICE_ACCOUNT}
type: kubernetes.io/service-account-token
EOF

  for _ in $(seq 1 30); do
    token=$(kubectl_host -n "$K8S_VAULT_NAMESPACE" get secret vault-auth-reviewer-token -o jsonpath='{.data.token}' 2>/dev/null || true)
    if [[ -n "$token" ]]; then
      printf '%s' "$token" | base64 --decode >"$REVIEWER_TOKEN_FILE"
      return 0
    fi
    sleep 2
  done

  echo "Timed out waiting for reviewer token" >&2
  exit 1
}

default_kubernetes_token_audience() {
  local token=""

  token=$(kubectl_host -n "$K8S_VAULT_NAMESPACE" create token "$K8S_REVIEWER_SERVICE_ACCOUNT" 2>/dev/null || true)
  if [[ -z "$token" && -f "$REVIEWER_TOKEN_FILE" ]]; then
    token=$(<"$REVIEWER_TOKEN_FILE")
  fi

  if [[ -z "$token" ]]; then
    return 1
  fi

  python3 - "$token" <<'PY'
import base64
import json
import sys

token = sys.argv[1]
parts = token.split(".")
if len(parts) < 2:
    raise SystemExit(1)

payload = parts[1] + "=" * (-len(parts[1]) % 4)
claims = json.loads(base64.urlsafe_b64decode(payload))
aud = claims.get("aud")
if isinstance(aud, list):
    if aud:
        print(aud[0])
elif isinstance(aud, str) and aud:
    print(aud)
PY
}

configure_vault_kubernetes_auth() {
  local root_token
  local k8s_accessor
  local assistant_template
  local reviewer_token
  local token_audience
  local audience_arg=""

  root_token=$(<"$ROOT_TOKEN_FILE")
  reviewer_token=$(<"$REVIEWER_TOKEN_FILE")
  token_audience=$(default_kubernetes_token_audience || true)
  if [[ -n "$token_audience" ]]; then
    audience_arg="audience=${token_audience}"
  fi

  write_policies "$root_token" \
    identity-payments-k8s-issuer \
    identity-mtls-backend-k8s-issuer \
    identity-assistant-k8s-spiffe \
    identity-assistant-k8s-jit

  if ! vault_exec "VAULT_TOKEN=$root_token vault auth list | grep -q '^kubernetes/'"; then
    vault_exec "VAULT_TOKEN=$root_token vault auth enable kubernetes" >/dev/null
  fi

  vault_exec "VAULT_TOKEN=$root_token vault write auth/kubernetes/config token_reviewer_jwt='$reviewer_token' kubernetes_host='https://host.docker.internal:${KIND_API_PORT}' kubernetes_ca_cert=@/vault/runtime/kind/kubernetes-ca.crt disable_iss_validation=true" >/dev/null

  vault_exec "VAULT_TOKEN=$root_token vault write auth/kubernetes/role/${K8S_PAYMENTS_SERVICE_ACCOUNT} bound_service_account_names=${K8S_PAYMENTS_SERVICE_ACCOUNT} bound_service_account_namespaces=${K8S_PAYMENTS_NAMESPACE} alias_name_source=serviceaccount_name ${audience_arg} token_type=batch token_ttl=15m token_max_ttl=15m token_policies=identity-payments-k8s-issuer" >/dev/null
  vault_exec "VAULT_TOKEN=$root_token vault write auth/kubernetes/role/${K8S_MTLS_BACKEND_SERVICE_ACCOUNT} bound_service_account_names=${K8S_MTLS_BACKEND_SERVICE_ACCOUNT} bound_service_account_namespaces=${K8S_PAYMENTS_NAMESPACE} alias_name_source=serviceaccount_name ${audience_arg} token_type=batch token_ttl=15m token_max_ttl=15m token_policies=identity-mtls-backend-k8s-issuer" >/dev/null
  vault_exec "VAULT_TOKEN=$root_token vault write auth/kubernetes/role/${K8S_ASSISTANT_SERVICE_ACCOUNT} bound_service_account_names=${K8S_ASSISTANT_SERVICE_ACCOUNT} bound_service_account_namespaces=${K8S_ASSISTANTS_NAMESPACE} alias_name_source=serviceaccount_name ${audience_arg} token_type=batch token_ttl=15m token_max_ttl=15m token_policies=identity-assistant-k8s-spiffe,identity-assistant-k8s-jit" >/dev/null
  vault_exec "VAULT_TOKEN=$root_token vault write pki/roles/payments-k8s-spiffe allow_any_name=true enforce_hostnames=false require_cn=false allowed_uri_sans='spiffe://hashibank.demo/ns/${K8S_PAYMENTS_NAMESPACE}/sa/${K8S_PAYMENTS_SERVICE_ACCOUNT}' max_ttl=8h" >/dev/null
  vault_exec "VAULT_TOKEN=$root_token vault write pki/roles/mtls-backend-k8s-spiffe allow_any_name=true enforce_hostnames=false require_cn=false allowed_uri_sans='spiffe://hashibank.demo/ns/${K8S_PAYMENTS_NAMESPACE}/sa/${K8S_MTLS_BACKEND_SERVICE_ACCOUNT}' max_ttl=8h" >/dev/null

  k8s_accessor=$(vault_exec "VAULT_TOKEN=$root_token vault auth list" | awk '$1 == "kubernetes/" {print $3}')
  assistant_template="$TEMPLATE_DIR/relationship-assistant-k8s-template.json"
  mkdir -p "$TEMPLATE_DIR"
  cat >"$assistant_template" <<EOF
{"sub":"spiffe://hashibank.demo/ns/{{identity.entity.aliases.${k8s_accessor}.metadata.service_account_namespace}}/sa/{{identity.entity.aliases.${k8s_accessor}.metadata.service_account_name}}","bank":"HashiBank","application":"relationship-assistant","line_of_business":"relationship-banking","environment":"demo","customer_data_domain":"masked-assistant-context","kubernetes_service_account":"{{identity.entity.aliases.${k8s_accessor}.metadata.service_account_namespace}}/{{identity.entity.aliases.${k8s_accessor}.metadata.service_account_name}}"}
EOF
  vault_exec "VAULT_TOKEN=$root_token vault write spiffe/role/relationship-assistant-k8s template=@/vault/runtime/templates/relationship-assistant-k8s-template.json ttl=15m use_jti_claim=true" >/dev/null
}

ensure_relationship_insights_table() {
  compose exec -T postgres-hashibank psql -v ON_ERROR_STOP=1 -U postgres -d hashibank >/dev/null <<'SQL'
CREATE TABLE IF NOT EXISTS customer_relationships (
  id SERIAL PRIMARY KEY,
  customer_mask TEXT NOT NULL,
  segment TEXT NOT NULL,
  relationship_tier TEXT NOT NULL,
  lifetime_value NUMERIC(14,2) NOT NULL,
  primary_product TEXT NOT NULL,
  next_best_action TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO customer_relationships (customer_mask, segment, relationship_tier, lifetime_value, primary_product, next_best_action, updated_at)
SELECT v.customer_mask, v.segment, v.relationship_tier, v.lifetime_value, v.primary_product, v.next_best_action, v.updated_at
FROM (VALUES
  ('**** 4417'::text, 'PRIVATE_WEALTH'::text, 'PLATINUM'::text, 1840500.00::numeric, 'Discretionary Portfolio'::text, 'Schedule annual wealth review'::text, NOW() - INTERVAL '3 days'),
  ('**** 9920', 'SME_BANKING', 'GOLD', 412300.00, 'Working Capital Facility', 'Offer FX hedging consultation', NOW() - INTERVAL '9 days'),
  ('**** 1185', 'RETAIL_PREMIER', 'GOLD', 96750.00, 'Offset Home Loan', 'Review redraw and rate options', NOW() - INTERVAL '1 day'),
  ('**** 7762', 'PRIVATE_WEALTH', 'PLATINUM', 2675000.00, 'Structured Investment', 'Introduce estate planning desk', NOW() - INTERVAL '14 days'),
  ('**** 3308', 'RETAIL_PREMIER', 'SILVER', 38400.00, 'Everyday Plus Account', 'Promote savings goal automation', NOW() - INTERVAL '6 hours')
) AS v(customer_mask, segment, relationship_tier, lifetime_value, primary_product, next_best_action, updated_at)
WHERE NOT EXISTS (SELECT 1 FROM customer_relationships);

GRANT SELECT ON TABLE public.customer_relationships TO vaultadmin WITH GRANT OPTION;
SQL
}

configure_vault_assistant_database_access() {
  local root_token="$1"

  mkdir -p "$RUNTIME_DIR/generated"

  cat >"$ASSISTANT_DB_CREATION_FILE" <<'EOF'
CREATE ROLE "{{name}}" WITH LOGIN PASSWORD '{{password}}' VALID UNTIL '{{expiration}}';
GRANT CONNECT ON DATABASE hashibank TO "{{name}}";
GRANT USAGE ON SCHEMA public TO "{{name}}";
GRANT SELECT ON TABLE public.customer_relationships TO "{{name}}";
EOF

  cat >"$ASSISTANT_DB_REVOCATION_FILE" <<'EOF'
REVOKE SELECT ON TABLE public.customer_relationships FROM "{{name}}";
REVOKE USAGE ON SCHEMA public FROM "{{name}}";
REVOKE CONNECT ON DATABASE hashibank FROM "{{name}}";
DROP ROLE IF EXISTS "{{name}}";
EOF

  if ! vault_exec "VAULT_TOKEN=$root_token vault secrets list | grep -q '^database/'"; then
    vault_exec "VAULT_TOKEN=$root_token vault secrets enable database" >/dev/null
  fi

  vault_exec "VAULT_TOKEN=$root_token vault write database/config/${ASSISTANT_DB_CONFIG_NAME} plugin_name=postgresql-database-plugin allowed_roles=${ASSISTANT_DB_ROLE} connection_url='${ASSISTANT_DB_CONNECTION_URL}' username='vaultadmin' password='vaultadminpw'" >/dev/null
  vault_exec "VAULT_TOKEN=$root_token vault write database/roles/${ASSISTANT_DB_ROLE} db_name=${ASSISTANT_DB_CONFIG_NAME} creation_statements=@/vault/runtime/generated/$(basename "$ASSISTANT_DB_CREATION_FILE") revocation_statements=@/vault/runtime/generated/$(basename "$ASSISTANT_DB_REVOCATION_FILE") default_ttl=5m max_ttl=30m" >/dev/null
}

configure_vault_assistant_jit_database() {
  echo "Starting Postgres for the just-in-time credential brokering proof..."
  compose up -d postgres-hashibank >/dev/null 2>&1
  wait_for_postgres
  ensure_relationship_insights_table
  configure_vault_assistant_database_access "$(<"$ROOT_TOKEN_FILE")"
}

publish_k8s_trust_assets() {
  kubectl_host -n "$K8S_PAYMENTS_NAMESPACE" create configmap hashibank-root-ca \
    --from-file=hashibank-root-ca.crt="$ROOT_CA_FILE" \
    --dry-run=client -o yaml | kubectl_host apply -f - >/dev/null
  kubectl_host -n "$K8S_ASSISTANTS_NAMESPACE" create configmap hashibank-root-ca \
    --from-file=hashibank-root-ca.crt="$ROOT_CA_FILE" \
    --dry-run=client -o yaml | kubectl_host apply -f - >/dev/null
  kubectl_host -n "$K8S_PAYMENTS_NAMESPACE" create configmap hashibank-spiffe-root \
    --from-file=hashibank-spiffe-root.pem="$TRUST_DIR/hashibank-spiffe-root.pem" \
    --dry-run=client -o yaml | kubectl_host apply -f - >/dev/null
}

apply_k8s_workloads() {
  kubectl_host -n "$K8S_PAYMENTS_NAMESPACE" delete pod "${K8S_PAYMENTS_SERVICE_ACCOUNT}" --ignore-not-found >/dev/null
  kubectl_host -n "$K8S_ASSISTANTS_NAMESPACE" delete pod "${K8S_ASSISTANT_SERVICE_ACCOUNT}" --ignore-not-found >/dev/null
  kubectl_host apply -f "$KIND_MANIFESTS_DIR/payments-workload.yaml" >/dev/null
  kubectl_host apply -f "$KIND_MANIFESTS_DIR/assistant-workload.yaml" >/dev/null
  kubectl_host apply -f "$KIND_MANIFESTS_DIR/mtls-backend.yaml" >/dev/null
  kubectl_host apply -f "$KIND_MANIFESTS_DIR/jwt-consumer.yaml" >/dev/null
  kubectl_host -n "$K8S_PAYMENTS_NAMESPACE" rollout restart deployment/mtls-backend >/dev/null 2>&1 || true
  kubectl_host -n "$K8S_ASSISTANTS_NAMESPACE" rollout restart deployment/jwt-consumer >/dev/null 2>&1 || true
}

wait_for_k8s_workloads() {
  kubectl_host -n "$K8S_PAYMENTS_NAMESPACE" wait --for=condition=Ready pod/"${K8S_PAYMENTS_SERVICE_ACCOUNT}" --timeout=180s >/dev/null
  kubectl_host -n "$K8S_ASSISTANTS_NAMESPACE" wait --for=condition=Ready pod/"${K8S_ASSISTANT_SERVICE_ACCOUNT}" --timeout=180s >/dev/null
  kubectl_host -n "$K8S_PAYMENTS_NAMESPACE" rollout status deployment/mtls-backend --timeout=180s >/dev/null
  kubectl_host -n "$K8S_ASSISTANTS_NAMESPACE" rollout status deployment/jwt-consumer --timeout=180s >/dev/null
}

run_connectivity_checks() {
  kubectl_host -n "$K8S_PAYMENTS_NAMESPACE" exec "$K8S_PAYMENTS_SERVICE_ACCOUNT" -- \
    bash -lc "for _ in \$(seq 1 30); do curl --silent --fail --max-time 5 --cacert /var/run/hashibank/roots/hashibank-root-ca.crt https://host.docker.internal:${VAULT_HOST_PORT}/v1/sys/health >/dev/null && exit 0; sleep 2; done; exit 1" >/dev/null
  kubectl_host -n "$K8S_ASSISTANTS_NAMESPACE" exec "$K8S_ASSISTANT_SERVICE_ACCOUNT" -- \
    bash -lc "for _ in \$(seq 1 30); do curl --silent --fail --max-time 5 http://jwt-consumer.${K8S_ASSISTANTS_NAMESPACE}.svc.cluster.local:8080/healthz >/dev/null && exit 0; sleep 2; done; exit 1" >/dev/null
}

bootstrap_base() {
  require_local_k8s_tooling
  ensure_bootstrap_directories
  ensure_vault_service
  ensure_kind_cluster
  write_kubeconfigs
  wait_for_kind_cluster
  build_and_load_kind_images
  apply_namespaces_and_rbac
  mint_reviewer_token
  configure_vault_kubernetes_auth
  configure_vault_assistant_jit_database
  publish_k8s_trust_assets
  apply_k8s_workloads
  wait_for_k8s_workloads
  run_connectivity_checks
}

base_bootstrap_ready() {
  local root_token

  if [[ ! -f "$ROOT_TOKEN_FILE" || ! -f "$VAULT_RUNTIME_DIR/init.txt" || ! -f "$KUBECONFIG_HOST_FILE" || ! -f "$KUBECONFIG_DOCKER_FILE" || ! -f "$TRUST_DIR/hashibank-spiffe-root.pem" ]]; then
    return 1
  fi

  if ! command_exists kind || ! kind_cluster_exists; then
    return 1
  fi

  compose up -d --build "$VAULT_SERVICE" demo-tools >/dev/null 2>&1
  wait_for_vault_service "$VAULT_SERVICE"
  initialise_and_unseal_vault_service "$VAULT_SERVICE"

  root_token=$(<"$ROOT_TOKEN_FILE")
  vault_exec "VAULT_TOKEN=$root_token vault read spiffe/config >/dev/null 2>&1"
}

ensure_base_bootstrap() {
  if base_bootstrap_ready; then
    return 0
  fi

  bootstrap_base
}

print_base_ready_message() {
  cat <<EOF

HashiBank Vault Kubernetes-native demo is ready.

Review:
  ./scripts/bootstrap.sh review

Vault-native workload identity sub-use cases:
  ./scripts/demo-k8s-mtls.sh
  ./scripts/demo-k8s-jwt.sh
  ./scripts/demo-k8s-jit.sh

Optional SPIRE overlay:
  ./scripts/bootstrap.sh spire

Tear down everything:
  ./scripts/teardown.sh
EOF
}

review_bootstrap() {
  if [[ ! -f "$ROOT_TOKEN_FILE" || ! -f "$KUBECONFIG_HOST_FILE" ]]; then
    echo "Bootstrap state not found. Run ./scripts/bootstrap.sh first." >&2
    exit 1
  fi

  print_heading "Vault Kubernetes auth configuration"
  show_vault_command_output "Kubernetes auth config" "vault read auth/kubernetes/config" "root"
  #show_vault_command_output "Payments Kubernetes auth role" "vault read auth/kubernetes/role/${K8S_PAYMENTS_SERVICE_ACCOUNT}" "root"
  #show_vault_command_output "mTLS backend Kubernetes auth role" "vault read auth/kubernetes/role/${K8S_MTLS_BACKEND_SERVICE_ACCOUNT}" "root"
  show_vault_command_output "Assistant Kubernetes auth role" "vault read auth/kubernetes/role/${K8S_ASSISTANT_SERVICE_ACCOUNT}" "root"
  pause_for_continue

  #print_heading "Issuer roles and trust assets"
  #show_vault_command_output "Payments PKI role" "vault read pki/roles/payments-k8s-spiffe" "root"
  #show_vault_command_output "mTLS backend PKI role" "vault read pki/roles/mtls-backend-k8s-spiffe" "root"
  print_heading "Vault SPIFFE engine configuration"
  show_vault_command_output "SPIFFE engine configuration" "vault read spiffe/config" "root"
  show_vault_command_output "Assistant SPIFFE role" "vault read spiffe/role/relationship-assistant-k8s" "root"
  pause_for_continue

  print_heading "Vault dynamic database brokering"
  show_vault_command_output "Assistant database connection" "vault read database/config/${ASSISTANT_DB_CONFIG_NAME}" "root"
  show_vault_command_output "Assistant dynamic database role" "vault read database/roles/${ASSISTANT_DB_ROLE}" "root"
  pause_for_continue

  #print_heading "Kubernetes workloads"
  #show_command_output \
  #  "Payments namespace pods and services" \
  #  "KUBECONFIG=$(printf '%q' "$KUBECONFIG_HOST_FILE") kubectl get pods,svc -n ${K8S_PAYMENTS_NAMESPACE}" \
  #  "KUBECONFIG=$(printf '%q' "$KUBECONFIG_HOST_FILE") kubectl get pods,svc -n ${K8S_PAYMENTS_NAMESPACE}"
  #show_command_output \
  #  "Assistants namespace pods and services" \
  #  "KUBECONFIG=$(printf '%q' "$KUBECONFIG_HOST_FILE") kubectl get pods,svc -n ${K8S_ASSISTANTS_NAMESPACE}" \
  #  "KUBECONFIG=$(printf '%q' "$KUBECONFIG_HOST_FILE") kubectl get pods,svc -n ${K8S_ASSISTANTS_NAMESPACE}"
  # show_command_output \
  #  "Demo service accounts" \
  #  "KUBECONFIG=$(printf '%q' "$KUBECONFIG_HOST_FILE") kubectl get serviceaccounts -n ${K8S_VAULT_NAMESPACE} && printf '\\n' && KUBECONFIG=$(printf '%q' "$KUBECONFIG_HOST_FILE") kubectl get serviceaccounts -n ${K8S_PAYMENTS_NAMESPACE} && printf '\\n' && KUBECONFIG=$(printf '%q' "$KUBECONFIG_HOST_FILE") kubectl get serviceaccounts -n ${K8S_ASSISTANTS_NAMESPACE}" \
  #  "KUBECONFIG=$(printf '%q' "$KUBECONFIG_HOST_FILE") kubectl get serviceaccounts -n ${K8S_VAULT_NAMESPACE} && printf '\\n' && KUBECONFIG=$(printf '%q' "$KUBECONFIG_HOST_FILE") kubectl get serviceaccounts -n ${K8S_PAYMENTS_NAMESPACE} && printf '\\n' && KUBECONFIG=$(printf '%q' "$KUBECONFIG_HOST_FILE") kubectl get serviceaccounts -n ${K8S_ASSISTANTS_NAMESPACE}"

  # printf '\nKubernetes-native review complete.\n'
}

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
  local spire_token

  root_token=$(<"$ROOT_TOKEN_FILE")

  write_policies "$root_token" access-spire-demo "$SPIRE_UPSTREAM_POLICY"

  if ! vault_exec "VAULT_TOKEN=$root_token vault secrets list | grep -q '^spire-pki/'"; then
    vault_exec "VAULT_TOKEN=$root_token vault secrets enable -path=spire-pki pki" >/dev/null
  fi

  vault_exec "VAULT_TOKEN=$root_token vault secrets tune -max-lease-ttl=8760h spire-pki" >/dev/null
  vault_exec "VAULT_TOKEN=$root_token vault write spire-pki/config/urls issuing_certificates='${VAULT_PUBLIC_ADDR}/v1/spire-pki/ca' crl_distribution_points='${VAULT_PUBLIC_ADDR}/v1/spire-pki/crl'" >/dev/null
  if ! vault_exec "VAULT_TOKEN=$root_token vault read spire-pki/cert/ca >/dev/null 2>&1"; then
    vault_exec "VAULT_TOKEN=$root_token vault write spire-pki/root/generate/internal common_name='HashiBank SPIRE Upstream Root' ttl=8760h" >/dev/null
  fi
  vault_exec "VAULT_TOKEN=$root_token vault read -field=certificate spire-pki/cert/ca" >"$SPIRE_UPSTREAM_BOOTSTRAP_BUNDLE_FILE"

  if ! vault_exec "VAULT_TOKEN=$root_token vault auth list | grep -q '^${SPIRE_JWT_AUTH_PATH}/'"; then
    vault_exec "VAULT_TOKEN=$root_token vault auth enable -path=${SPIRE_JWT_AUTH_PATH} -passthrough-request-headers=Authorization spiffe" >/dev/null
  else
    vault_exec "VAULT_TOKEN=$root_token vault auth tune -passthrough-request-headers=Authorization ${SPIRE_JWT_AUTH_PATH}/" >/dev/null
  fi

  spire_token=$(vault_exec "VAULT_TOKEN=$root_token vault token create -policy=${SPIRE_UPSTREAM_POLICY} -display-name=spire-upstreamauthority -orphan -ttl=8760h -field=token")
  printf '%s' "$spire_token" >"$SPIRE_UPSTREAM_TOKEN_FILE"
}

configure_vault_spire_database_access() {
  local root_token="$1"

  mkdir -p "$RUNTIME_DIR/generated"

  cat >"$SPIRE_DB_CREATION_FILE" <<'EOF'
CREATE ROLE "{{name}}" WITH LOGIN PASSWORD '{{password}}' VALID UNTIL '{{expiration}}';
GRANT CONNECT ON DATABASE hashibank TO "{{name}}";
GRANT USAGE ON SCHEMA public TO "{{name}}";
GRANT SELECT ON TABLE public.fraud_alerts TO "{{name}}";
EOF

  cat >"$SPIRE_DB_REVOCATION_FILE" <<'EOF'
REVOKE SELECT ON TABLE public.fraud_alerts FROM "{{name}}";
REVOKE USAGE ON SCHEMA public FROM "{{name}}";
REVOKE CONNECT ON DATABASE hashibank FROM "{{name}}";
DROP ROLE IF EXISTS "{{name}}";
EOF

  if ! vault_exec "VAULT_TOKEN=$root_token vault secrets list | grep -q '^database/'"; then
    vault_exec "VAULT_TOKEN=$root_token vault secrets enable database" >/dev/null
  fi

  vault_exec "VAULT_TOKEN=$root_token vault write database/config/${SPIRE_DB_CONFIG_NAME} plugin_name=postgresql-database-plugin allowed_roles=${SPIRE_DB_ROLE} connection_url='${SPIRE_DB_CONNECTION_URL}' username='vaultadmin' password='vaultadminpw'" >/dev/null
  vault_exec "VAULT_TOKEN=$root_token vault write database/roles/${SPIRE_DB_ROLE} db_name=${SPIRE_DB_CONFIG_NAME} creation_statements=@/vault/runtime/generated/$(basename "$SPIRE_DB_CREATION_FILE") revocation_statements=@/vault/runtime/generated/$(basename "$SPIRE_DB_REVOCATION_FILE") default_ttl=15m max_ttl=1h" >/dev/null
}

configure_vault_spire_auth() {
  local root_token

  root_token=$(<"$ROOT_TOKEN_FILE")

  vault_exec "VAULT_TOKEN=$root_token vault write auth/${SPIRE_JWT_AUTH_PATH}/config trust_domain=${SPIRE_TRUST_DOMAIN} profile=https_web_bundle endpoint_url=${SPIRE_BUNDLE_ENDPOINT_URL} endpoint_root_ca_truststore_pem=@/vault/config/tls/hashibank-root-ca.crt audience=${SPIRE_JWT_AUDIENCE}" >/dev/null
  vault_exec "VAULT_TOKEN=$root_token vault write auth/${SPIRE_JWT_AUTH_PATH}/role/${SPIRE_VAULT_ROLE} display_name=${SPIRE_VAULT_ROLE} token_type=batch token_policies=access-spire-demo workload_id_patterns=${SPIRE_WORKLOAD_ID_PATH}" >/dev/null
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

ensure_spire_services_running() {
  compose up -d --build "$VAULT_SERVICE" "$SPIRE_SERVER_SERVICE" "$SPIRE_AGENT_SERVICE" "$SPIRE_CLIENT_SERVICE" demo-tools >/dev/null 2>&1
  wait_for_vault_service "$VAULT_SERVICE"
  wait_for_spire_bundle_endpoint
  wait_for_spire_agent_api
}

show_spire_status() {
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
  show_command_output \
    "Vault SPIRE JWT auth role" \
    "VAULT_ADDR=${VAULT_HOST_ADDR} VAULT_CACERT=config/tls/hashibank-root-ca.crt VAULT_TOKEN=\$(cat runtime/hashibank-vault/root-token) vault read auth/${SPIRE_JWT_AUTH_PATH}/role/${SPIRE_VAULT_ROLE}"
  show_command_output \
    "Vault fraud database role" \
    "VAULT_ADDR=${VAULT_HOST_ADDR} VAULT_CACERT=config/tls/hashibank-root-ca.crt VAULT_TOKEN=\$(cat runtime/hashibank-vault/root-token) vault read database/roles/${SPIRE_DB_ROLE}"
}

bootstrap_spire_overlay() {
  local root_token

  ensure_base_bootstrap

  compose rm -sf "$SPIRE_SERVER_SERVICE" "$SPIRE_AGENT_SERVICE" "$SPIRE_CLIENT_SERVICE" >/dev/null 2>&1 || true
  rm -rf "$SPIRE_RUNTIME_DIR"

  mkdir -p \
    "$SPIRE_SERVER_DATA_DIR" \
    "$SPIRE_SERVER_SOCKET_DIR" \
    "$SPIRE_AGENT_DATA_DIR" \
    "$SPIRE_AGENT_BOOTSTRAP_DIR" \
    "$SPIRE_AGENT_SOCKET_DIR"

  ensure_demo_server_cert \
    "$SPIRE_SERVER_SERVICE" \
    "$SPIRE_SERVER_SERVICE" \
    "DNS:localhost" \
    "IP:127.0.0.1"

  echo "Starting Postgres for the SPIRE database brokering proof..."
  compose up -d postgres-hashibank >/dev/null 2>&1
  wait_for_postgres

  echo "Configuring Vault for SPIRE integration..."
  configure_vault_for_spire_upstream
  root_token=$(<"$ROOT_TOKEN_FILE")
  configure_vault_spire_database_access "$root_token"

  echo "Starting SPIRE server..."
  SPIRE_VAULT_TOKEN=$(<"$SPIRE_UPSTREAM_TOKEN_FILE") \
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

Status:
  ./scripts/bootstrap.sh spire-status

Supported demos:
  ./scripts/demo-spire-jwt.sh
  ./scripts/demo-spire-upstreamauthority.sh

Known limitation:
  SPIRE X.509-SVID -> Vault SPIFFE auth is not enabled here because auth still
  failed when Vault trusted the SPIRE federation bundle/root.
  The same workload authenticated only when Vault trusted the SPIRE issuing
  intermediate directly. That workaround is intentionally omitted because it
  diverges from the intended "fetch trust bundle from SPIRE" model.
EOF
}

case "$ACTION" in
  up|bootstrap)
    bootstrap_base
    print_base_ready_message
    ;;
  review)
    review_bootstrap
    ;;
  spire)
    bootstrap_spire_overlay
    ;;
  spire-status)
    require_spire_overlay_bootstrap
    ensure_spire_services_running
    show_spire_status
    ;;
  *)
    cat >&2 <<EOF
Usage:
  ./scripts/bootstrap.sh
  ./scripts/bootstrap.sh review
  ./scripts/bootstrap.sh spire
  ./scripts/bootstrap.sh spire-status
EOF
    exit 1
    ;;
esac
