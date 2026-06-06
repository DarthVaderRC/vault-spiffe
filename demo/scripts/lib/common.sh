#!/usr/bin/env bash
set -euo pipefail

COMMON_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SCRIPTS_DIR=$(cd "$COMMON_DIR/.." && pwd)
DEMO_DIR=$(cd "$SCRIPTS_DIR/.." && pwd)
COMPOSE_FILE="$DEMO_DIR/docker-compose.yml"
RUNTIME_DIR="$DEMO_DIR/runtime"
TLS_DIR="$DEMO_DIR/config/tls"
ROOT_CA_FILE="$TLS_DIR/hashibank-root-ca.crt"
VAULT_SERVICE="hashibank-vault"
VAULT_HOST_PORT="${HASHIBANK_VAULT_HOST_PORT:-18200}"
VAULT_HOST_ADDR="https://localhost:${VAULT_HOST_PORT}"
VAULT_PUBLIC_HOSTNAME="${VAULT_PUBLIC_HOSTNAME:-vault.demo.internal}"
VAULT_PUBLIC_ADDR="https://${VAULT_PUBLIC_HOSTNAME}:${VAULT_HOST_PORT}"
VAULT_DOCKER_ADDR="https://host.docker.internal:${VAULT_HOST_PORT}"
VAULT_RUNTIME_DIR="$RUNTIME_DIR/hashibank-vault"
VAULT_RUNTIME_STORAGE_DIR="$VAULT_RUNTIME_DIR/raft"
ROOT_TOKEN_FILE="$VAULT_RUNTIME_DIR/root-token"
PERF_VAULT_SERVICE="hashibank-vault-perf"
PERF_VAULT_HOST_PORT="${HASHIBANK_VAULT_PERF_HOST_PORT:-19200}"
PERF_VAULT_HOST_ADDR="https://localhost:${PERF_VAULT_HOST_PORT}"
PERF_VAULT_RUNTIME_DIR="$RUNTIME_DIR/hashibank-vault-perf"
PERF_VAULT_RUNTIME_STORAGE_DIR="$PERF_VAULT_RUNTIME_DIR/raft"
PERF_ROOT_TOKEN_FILE="$PERF_VAULT_RUNTIME_DIR/root-token"
SPIRE_SERVER_SERVICE="spire-server"
SPIRE_AGENT_SERVICE="spire-agent"
SPIRE_CLIENT_SERVICE="hashibank-spire-client"
SPIRE_BUNDLE_ENDPOINT_HOST_PORT="${SPIRE_BUNDLE_ENDPOINT_HOST_PORT:-18443}"
SPIRE_BUNDLE_ENDPOINT_HOST_ADDR="https://localhost:${SPIRE_BUNDLE_ENDPOINT_HOST_PORT}"
SPIRE_RUNTIME_DIR="$RUNTIME_DIR/spire"
SPIRE_SERVER_RUNTIME_DIR="$SPIRE_RUNTIME_DIR/server"
SPIRE_SERVER_DATA_DIR="$SPIRE_SERVER_RUNTIME_DIR/data"
SPIRE_SERVER_SOCKET_DIR="$SPIRE_SERVER_RUNTIME_DIR/socket"
SPIRE_SERVER_ENV_FILE="$SPIRE_SERVER_RUNTIME_DIR/server.env"
SPIRE_SERVER_SOCKET_PATH="/run/spire/server/private/api.sock"
SPIRE_AGENT_RUNTIME_DIR="$SPIRE_RUNTIME_DIR/agent"
SPIRE_AGENT_DATA_DIR="$SPIRE_AGENT_RUNTIME_DIR/data"
SPIRE_AGENT_BOOTSTRAP_DIR="$SPIRE_AGENT_RUNTIME_DIR/bootstrap"
SPIRE_AGENT_BOOTSTRAP_BUNDLE_FILE="$SPIRE_AGENT_BOOTSTRAP_DIR/bootstrap-trust-bundle.pem"
SPIRE_AGENT_SOCKET_DIR="$SPIRE_AGENT_RUNTIME_DIR/socket"
SPIRE_AGENT_CONFIG_FILE="$SPIRE_AGENT_RUNTIME_DIR/agent.conf"
SPIRE_AGENT_SOCKET_PATH="/run/spire/agent/public/api.sock"
SPIRE_CLIENT_UID="10001"
FRAUD_WEB_PORT="${HASHIBANK_FRAUD_WEB_PORT:-18081}"
ASSISTANT_WEB_PORT="${HASHIBANK_ASSISTANT_WEB_PORT:-18082}"
KIND_CLUSTER_NAME="${KIND_CLUSTER_NAME:-vault-spiffe}"
KIND_API_PORT="${KIND_API_PORT:-16443}"
KIND_RUNTIME_DIR="$RUNTIME_DIR/kind"
KUBECONFIG_HOST_FILE="$KIND_RUNTIME_DIR/kubeconfig"
KUBECONFIG_DOCKER_FILE="$KIND_RUNTIME_DIR/kubeconfig-docker"
K8S_VAULT_NAMESPACE="${K8S_VAULT_NAMESPACE:-vault-system}"
K8S_PAYMENTS_NAMESPACE="${K8S_PAYMENTS_NAMESPACE:-payments}"
K8S_ASSISTANTS_NAMESPACE="${K8S_ASSISTANTS_NAMESPACE:-assistants}"
K8S_REVIEWER_SERVICE_ACCOUNT="${K8S_REVIEWER_SERVICE_ACCOUNT:-vault-auth-reviewer}"
K8S_PAYMENTS_SERVICE_ACCOUNT="${K8S_PAYMENTS_SERVICE_ACCOUNT:-payments-api}"
K8S_MTLS_BACKEND_SERVICE_ACCOUNT="${K8S_MTLS_BACKEND_SERVICE_ACCOUNT:-mtls-backend}"
K8S_ASSISTANT_SERVICE_ACCOUNT="${K8S_ASSISTANT_SERVICE_ACCOUNT:-relationship-assistant}"

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

compose() {
  docker compose -f "$COMPOSE_FILE" "$@"
}

write_policies() {
  local root_token="$1"
  shift

  for policy in "$@"; do
    vault_exec "VAULT_TOKEN=$root_token vault policy write ${policy%.hcl} /vault/policies/${policy}.hcl" >/dev/null
  done
}

vault_service_runtime_dir() {
  case "$1" in
    "$VAULT_SERVICE")
      printf '%s\n' "$VAULT_RUNTIME_DIR"
      ;;
    "$PERF_VAULT_SERVICE")
      printf '%s\n' "$PERF_VAULT_RUNTIME_DIR"
      ;;
    *)
      echo "Unknown Vault service: $1" >&2
      return 1
      ;;
  esac
}

vault_service_storage_dir() {
  case "$1" in
    "$VAULT_SERVICE")
      printf '%s\n' "$VAULT_RUNTIME_STORAGE_DIR"
      ;;
    "$PERF_VAULT_SERVICE")
      printf '%s\n' "$PERF_VAULT_RUNTIME_STORAGE_DIR"
      ;;
    *)
      echo "Unknown Vault service: $1" >&2
      return 1
      ;;
  esac
}

vault_service_host_addr() {
  case "$1" in
    "$VAULT_SERVICE")
      printf '%s\n' "$VAULT_HOST_ADDR"
      ;;
    "$PERF_VAULT_SERVICE")
      printf '%s\n' "$PERF_VAULT_HOST_ADDR"
      ;;
    *)
      echo "Unknown Vault service: $1" >&2
      return 1
      ;;
  esac
}

vault_service_root_token_file() {
  case "$1" in
    "$VAULT_SERVICE")
      printf '%s\n' "$ROOT_TOKEN_FILE"
      ;;
    "$PERF_VAULT_SERVICE")
      printf '%s\n' "$PERF_ROOT_TOKEN_FILE"
      ;;
    *)
      echo "Unknown Vault service: $1" >&2
      return 1
      ;;
  esac
}

vault_exec_service() {
  local service="$1"
  shift

  compose exec -T "$service" sh -lc "export VAULT_ADDR=https://127.0.0.1:8200 VAULT_CACERT=/vault/config/tls/hashibank-root-ca.crt; $*"
}

vault_exec() {
  # Run Vault CLI commands inside the service container so they use container-local
  # DNS names and the demo CA bundle instead of host networking assumptions.
  vault_exec_service "$VAULT_SERVICE" "$@"
}

spire_server_exec() {
  compose exec -T "$SPIRE_SERVER_SERVICE" /opt/spire/bin/spire-server "$@"
}

spire_agent_exec() {
  compose exec -T "$SPIRE_AGENT_SERVICE" /opt/spire/bin/spire-agent "$@"
}

spire_client_exec() {
  compose exec -T "$SPIRE_CLIENT_SERVICE" bash -lc "$*"
}

wait_for_vault_service() {
  local service="$1"

  wait_for_https "$service" "$(vault_service_host_addr "$service")"
}

wait_for_spire_server_api() {
  for _ in $(seq 1 60); do
    if spire_server_exec bundle show -socketPath "$SPIRE_SERVER_SOCKET_PATH" -output json >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done

  echo "Timed out waiting for SPIRE server API" >&2
  return 1
}

wait_for_spire_agent_api() {
  for _ in $(seq 1 60); do
    if spire_agent_exec healthcheck -socketPath "$SPIRE_AGENT_SOCKET_PATH" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done

  echo "Timed out waiting for SPIRE agent API" >&2
  return 1
}

wait_for_spire_bundle_endpoint() {
  for _ in $(seq 1 60); do
    if curl --silent --fail --cacert "$ROOT_CA_FILE" "$SPIRE_BUNDLE_ENDPOINT_HOST_ADDR/" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done

  echo "Timed out waiting for $SPIRE_SERVER_SERVICE bundle endpoint at $SPIRE_BUNDLE_ENDPOINT_HOST_ADDR" >&2
  return 1
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

wait_for_http() {
  local name="$1"
  local url="$2"

  for _ in $(seq 1 30); do
    if curl --silent --show-error --fail "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done

  echo "$name did not become ready in time" >&2
  return 1
}

read_status_value() {
  local field="$1"

  vault_exec "vault status" | awk -v target="$field" '$1 == target {print $2}'
}

read_vault_status_value() {
  local service="$1"
  local field="$2"

  vault_exec_service "$service" "vault status" | awk -v target="$field" '$1 == target {print $2}'
}

extract_init_value() {
  local file="$1"
  local key="$2"

  awk -F': ' -v target="$key" '$1 == target {print $2}' "$file"
}

ensure_demo_tls_root_ca() {
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

ensure_demo_server_cert() {
  local name="$1"
  local common_name="$2"
  shift 2
  local key_file="$TLS_DIR/$name.key"
  local csr_file="$TLS_DIR/$name.csr"
  local cert_file="$TLS_DIR/$name.crt"
  local ext_file="$TLS_DIR/$name.ext"
  local san_entries=("DNS:${common_name}")
  local san_csv

  if (($#)); then
    san_entries+=("$@")
  fi

  if [[ -f "$cert_file" && -f "$key_file" ]] && cert_has_san_entries "$cert_file" "${san_entries[@]}"; then
    return 0
  fi

  san_csv=$(IFS=,; printf '%s' "${san_entries[*]}")
  cat >"$ext_file" <<EOF
subjectAltName=${san_csv}
extendedKeyUsage=serverAuth
EOF

  openssl genrsa -out "$key_file" 2048 >/dev/null 2>&1
  openssl req -new -key "$key_file" -subj "/CN=${common_name}" -out "$csr_file" >/dev/null 2>&1
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

cert_has_san_entries() {
  local cert_file="$1"
  shift
  local text

  if [[ ! -f "$cert_file" ]]; then
    return 1
  fi

  text=$(openssl x509 -in "$cert_file" -noout -text 2>/dev/null || true)
  if [[ -z "$text" ]]; then
    return 1
  fi

  for san_entry in "$@"; do
    case "$san_entry" in
      DNS:*)
        if ! grep -Fq "${san_entry}" <<<"$text"; then
          return 1
        fi
        ;;
      IP:*)
        if ! grep -Fq "IP Address:${san_entry#IP:}" <<<"$text"; then
          return 1
        fi
        ;;
      *)
        return 1
        ;;
    esac
  done

  return 0
}

ensure_hashibank_demo_bootstrap() {
  local root_token

  if [[ -f "$ROOT_TOKEN_FILE" && -f "$VAULT_RUNTIME_DIR/init.txt" && -f "$KUBECONFIG_HOST_FILE" ]] \
    && command_exists kind \
    && kind get clusters 2>/dev/null | grep -Fxq "$KIND_CLUSTER_NAME"; then
    compose up -d --build "$VAULT_SERVICE" demo-tools >/dev/null 2>&1
    wait_for_vault_service "$VAULT_SERVICE"
    initialise_and_unseal_vault_service "$VAULT_SERVICE"

    root_token=$(<"$ROOT_TOKEN_FILE")
    if vault_exec "VAULT_TOKEN=$root_token vault read spiffe/config >/dev/null 2>&1"; then
      return 0
    fi
  fi

  echo "Bootstrapping hashibank-vault for the demo environment..."
  HASHIBANK_DEMO_NO_PAUSE=1 "$SCRIPTS_DIR/bootstrap.sh"
}

require_spire_overlay_bootstrap() {
  if [[ -f "$ROOT_TOKEN_FILE" && -f "$SPIRE_AGENT_CONFIG_FILE" && -f "$SPIRE_AGENT_BOOTSTRAP_BUNDLE_FILE" ]]; then
    return 0
  fi

  echo "SPIRE overlay is not bootstrapped. Run ./scripts/bootstrap.sh spire first." >&2
  return 1
}

initialise_and_unseal_vault_service() {
  local service="$1"
  local runtime_dir
  local init_file
  local root_token_file
  local initialized
  local sealed
  local unseal_key
  local root_token

  runtime_dir=$(vault_service_runtime_dir "$service")
  init_file="$runtime_dir/init.txt"
  root_token_file=$(vault_service_root_token_file "$service")
  mkdir -p "$runtime_dir"

  initialized=$(read_vault_status_value "$service" "Initialized" || true)
  if [[ "$initialized" != "true" ]]; then
    echo "Initializing $service..."
    vault_exec_service "$service" "vault operator init -key-shares=1 -key-threshold=1" >"$init_file"
  elif [[ ! -f "$init_file" ]]; then
    echo "Expected $init_file for already initialized $service" >&2
    return 1
  fi

  unseal_key=$(extract_init_value "$init_file" "Unseal Key 1")
  root_token=$(extract_init_value "$init_file" "Initial Root Token")
  printf '%s' "$root_token" >"$root_token_file"

  sealed=$(read_vault_status_value "$service" "Sealed" || true)
  if [[ "$sealed" == "true" ]]; then
    echo "Unsealing $service..."
    vault_exec_service "$service" "vault operator unseal $unseal_key" >/dev/null
  fi
}

show_heading() {
  local title="$1"

  printf '\n=== %s ===\n\n' "$title"
}

# Formatter Function
print_heading() {
    local title="$1"
    local style="${2:-34}" # Default is blue text (34)
    local fill_char="${3:-#}"
    local term_width=$(tput cols)
    
    # Calculate padding
    local text_len=${#title}
    local total_len=$((term_width > 80 ? 80 : term_width))
    local padding=$(( (total_len - text_len - 4) / 2 ))
    
    # Build borders
    local border=$(printf "%0.s${fill_char}" $(seq 1 $total_len))
    local spaces=$(printf "%0.s " $(seq 1 $padding))
    
    # Output
    echo
    printf "\e[1;${style}m%s\e[0m\n" "$border"
    printf "\e[1;${style}m%s  %s  %s\e[0m\n" "$fill_char" "$title" "$fill_char"
    printf "\e[1;${style}m%s\e[0m\n" "$border"
    echo
}

pause_for_continue() {
  local key

  if [[ ! -t 0 || ! -t 1 || "${HASHIBANK_DEMO_NO_PAUSE:-0}" == "1" ]]; then
    return 0
  fi

  while true; do
    read -r -n 1 -s -p "Press n to continue..." key
    if [[ "$key" == "n" || "$key" == "N" ]]; then
      break
    fi
  done
  printf '\n\n'
}

show_command_output() {
  local title="$1"
  local command="$2"
  local exec_command="${3:-$2}"
  local output

  while [[ "$command" == $'\n'* ]]; do
    command="${command#$'\n'}"
  done
  while [[ "$command" == *$'\n' ]]; do
    command="${command%$'\n'}"
  done

  show_heading "$title"
  printf '$ %s\n\n' "$command"
  output=$(cd "$DEMO_DIR" && bash -lc "set -euo pipefail; $exec_command")
  if [[ -n "$output" ]]; then
    printf '%s\n' "$output"
  else
    printf '(no output)\n'
  fi
}

show_vault_command_output() {
  local title="$1"
  local command="$2"
  local token_mode="${3:-root}"
  local setup

  setup="export VAULT_ADDR='$VAULT_HOST_ADDR'; export VAULT_CACERT='config/tls/hashibank-root-ca.crt';"
  if [[ "$token_mode" == "root" ]]; then
    setup="$setup export VAULT_TOKEN=\$(cat runtime/hashibank-vault/root-token);"
  fi

  show_command_output "$title" "$command" "$setup $command"
}
