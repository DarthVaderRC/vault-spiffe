#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

ACTION="${1:-run}"

run_internal() {
  local command="$1"

  compose exec -T demo-tools bash -lc \
    "export PYTHONPATH=/workspace/demo/python KUBECONFIG=/workspace/demo/runtime/kind/kubeconfig-docker; python /workspace/demo/scripts/internal/k8s_jwt_demo.py '$command'"
}

run_flow() {
  local steps=(
    kubernetes-login
    mint-jwt
    fetch-discovery
    call-consumer
  )
  local idx

  rm -f "$RUNTIME_DIR/checkpoints/k8s-jwt.json"

  for idx in "${!steps[@]}"; do
    run_internal "${steps[$idx]}"
    if (( idx + 1 < ${#steps[@]} )); then
      pause_for_continue
    fi
  done
}

wait_for_assistant_web() {
  local url="http://localhost:${ASSISTANT_WEB_PORT}/healthz"

  for _ in $(seq 1 30); do
    if curl --silent --show-error --fail "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done

  echo "Timed out waiting for hashibank-assistant at $url" >&2
  exit 1
}

if [[ ! -f "$KUBECONFIG_DOCKER_FILE" ]]; then
  echo "Kubernetes overlay is not bootstrapped. Run ./scripts/bootstrap.sh first." >&2
  exit 1
fi

case "$ACTION" in
  run|status|reset)
    ;;
  *)
    echo "Usage: ./scripts/demo-k8s-jwt.sh [status|reset]" >&2
    exit 1
    ;;
esac

HASHIBANK_DEMO_SCENARIO=k8s-jwt compose up -d --build demo-tools hashibank-assistant >/dev/null 2>&1
wait_for_assistant_web

if [[ "$ACTION" == "run" ]]; then
  run_flow
  curl --silent --show-error --fail "http://localhost:${ASSISTANT_WEB_PORT}/api/demo" >/dev/null
else
  run_internal "$ACTION"
fi
