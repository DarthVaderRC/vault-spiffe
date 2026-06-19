#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

ACTION="${1:-run}"

run_internal() {
  local command="$1"

  compose exec -T demo-tools bash -lc \
    "export PYTHONPATH=/workspace/demo/python KUBECONFIG=/workspace/demo/runtime/kind/kubeconfig-docker; python /workspace/demo/scripts/internal/k8s_jit_demo.py '$command'"
}

run_flow() {
  local steps=(
    kubernetes-login
    broker-db-creds
    query-insights
    revoke-lease
  )
  local idx

  rm -f "$RUNTIME_DIR/checkpoints/k8s-jit.json"

  for idx in "${!steps[@]}"; do
    run_internal "${steps[$idx]}"
    if (( idx + 1 < ${#steps[@]} )); then
      pause_for_continue
    fi
  done
}

if [[ ! -f "$KUBECONFIG_DOCKER_FILE" ]]; then
  echo "Kubernetes overlay is not bootstrapped. Run ./scripts/bootstrap.sh first." >&2
  exit 1
fi

case "$ACTION" in
  run|status|reset)
    ;;
  *)
    echo "Usage: ./scripts/demo-k8s-jit.sh [status|reset]" >&2
    exit 1
    ;;
esac

compose up -d --build demo-tools postgres-hashibank >/dev/null 2>&1
wait_for_postgres

if [[ "$ACTION" == "run" ]]; then
  run_flow
else
  run_internal "$ACTION"
fi
