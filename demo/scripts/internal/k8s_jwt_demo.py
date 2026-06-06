from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from hashibank_demo.checkpoints import (
    DemoStep,
    load_state,
    record_step,
    require_step_dependencies,
    reset_state,
    save_state,
    scenario_state_path,
    step_artifacts,
)
from hashibank_demo.transcript import (
    print_reset,
    print_status,
    run_text_command,
    run_vault_command,
)
from hashibank_demo.vault_client import decode_unverified_jwt, read_text


SCENARIO = "k8s-jwt"
PERSONA = "relationship-assistant"
SCRIPT_NAME = "demo-k8s-jwt.sh"
CHECKPOINT_FILE = scenario_state_path(SCENARIO)
DEMO_ROOT = Path("/workspace/demo")
RUNTIME_DIR = DEMO_ROOT / "runtime"
ROOT_TOKEN_FILE = RUNTIME_DIR / "hashibank-vault" / "root-token"
KUBERNETES_ROLE = "relationship-assistant"
SPIFFE_ROLE = "relationship-assistant-k8s"
KUBERNETES_NAMESPACE = "assistants"
POD_NAME = "relationship-assistant"
VAULT_REACHABLE_ADDR = "https://host.docker.internal:18200"
POD_TOKEN_FILE = "/var/run/secrets/kubernetes.io/serviceaccount/token"
POD_ROOT_CA = "/var/run/hashibank/roots/hashibank-root-ca.crt"
JWT_FILE = "/tmp/hashibank-demo/assistant.jwt"
JWT_AUDIENCE = "relationship-insights-api"

STEPS = [
    DemoStep("kubernetes-login", "Kubernetes auth login", "issuer-auth"),
    DemoStep("mint-jwt", "Vault JWT-SVID mint", "identity-artifact"),
    DemoStep("fetch-discovery", "OIDC discovery and JWKS", "trust-decision"),
    DemoStep("call-consumer", "Protected JWT consumer call", "business-proof"),
]


def _json_output(title: str, command: str) -> dict:
    output = run_text_command(title, command)
    return json.loads(output)


def kubernetes_login_step(state: dict) -> dict:
    root_token = read_text(ROOT_TOKEN_FILE)

    run_vault_command(
        "Assistant Kubernetes auth role",
        f"vault read auth/kubernetes/role/{KUBERNETES_ROLE}",
        token=root_token,
    )
    run_text_command(
        "Assistant service account",
        f"kubectl get serviceaccount -n {KUBERNETES_NAMESPACE} {KUBERNETES_ROLE} -o yaml",
    )

    login_data = _json_output(
        "Assistant Kubernetes auth login",
        f"""kubectl exec -n {KUBERNETES_NAMESPACE} {POD_NAME} -- bash -lc '
JWT=$(cat {POD_TOKEN_FILE})
payload=$(jq -nc --arg role {json.dumps(KUBERNETES_ROLE)} --arg jwt "$JWT" "{{\\\"role\\\": \\$role, \\\"jwt\\\": \\$jwt}}")
curl --silent --show-error --fail \
  --cacert {POD_ROOT_CA} \
  --header "Content-Type: application/json" \
  --request POST \
  --data "$payload" \
  {VAULT_REACHABLE_ADDR}/v1/auth/kubernetes/login | jq -c .
'""",
    )
    auth = login_data["auth"]
    summary = {
        "service_account_name": auth["metadata"]["service_account_name"],
        "service_account_namespace": auth["metadata"]["service_account_namespace"],
        "vault_policies": auth.get("policies", []),
    }
    record_step(
        state,
        STEPS,
        "kubernetes-login",
        summary=summary,
        artifacts={"client_token": auth["client_token"]},
    )
    return summary


def mint_jwt_step(state: dict) -> dict:
    require_step_dependencies(state, STEPS, "mint-jwt")
    root_token = read_text(ROOT_TOKEN_FILE)
    issuer_token = step_artifacts(state, "kubernetes-login")["client_token"]

    run_vault_command(
        "Assistant SPIFFE role for Kubernetes workloads",
        f"vault read spiffe/role/{SPIFFE_ROLE}",
        token=root_token,
    )

    mint_data = _json_output(
        "Assistant JWT-SVID mint response",
        f"""kubectl exec -n {KUBERNETES_NAMESPACE} {POD_NAME} -- bash -lc '
mkdir -p /tmp/hashibank-demo
payload=$(jq -nc --arg audience {json.dumps(JWT_AUDIENCE)} "{{\\\"audience\\\": \\$audience}}")
response=$(curl --silent --show-error --fail \
  --cacert {POD_ROOT_CA} \
  --header "Content-Type: application/json" \
  --header "X-Vault-Token: {issuer_token}" \
  --request POST \
  --data "$payload" \
  {VAULT_REACHABLE_ADDR}/v1/spiffe/role/{SPIFFE_ROLE}/mintjwt)
printf "%s" "$response" | jq -r ".data.token" > {JWT_FILE}
printf "%s" "$response" | jq -c .
'""",
    )

    jwt_token = mint_data["data"]["token"]
    jwt_claims = decode_unverified_jwt(jwt_token)
    run_text_command(
        "Decoded assistant JWT-SVID claims",
        f"""python - <<'PY'
import json
from hashibank_demo.vault_client import decode_unverified_jwt

token = {json.dumps(jwt_token)}
print(json.dumps(decode_unverified_jwt(token), indent=2))
PY""",
        env={"PYTHONPATH": "/workspace/demo/python"},
    )

    summary = {
        "sub": jwt_claims["sub"],
        "aud": jwt_claims["aud"],
        "bank": jwt_claims.get("bank"),
        "application": jwt_claims.get("application"),
        "line_of_business": jwt_claims.get("line_of_business"),
        "environment": jwt_claims.get("environment"),
        "customer_data_domain": jwt_claims.get("customer_data_domain"),
        "kubernetes_service_account": jwt_claims.get("kubernetes_service_account"),
    }
    record_step(
        state,
        STEPS,
        "mint-jwt",
        summary=summary,
        artifacts={"jwt_token": jwt_token},
    )
    return summary


def fetch_discovery_step(state: dict) -> dict:
    require_step_dependencies(state, STEPS, "fetch-discovery")

    discovery = _json_output(
        "SPIFFE discovery document from Vault",
        f"""curl --silent --show-error --fail \
  --cacert config/tls/hashibank-root-ca.crt \
  {VAULT_REACHABLE_ADDR}/v1/spiffe/.well-known/openid-configuration | jq -c .""",
    )
    jwks = _json_output(
        "SPIFFE JWKS from Vault",
        f"""curl --silent --show-error --fail \
  --cacert config/tls/hashibank-root-ca.crt \
  {VAULT_REACHABLE_ADDR}/v1/spiffe/.well-known/keys | jq -c .""",
    )
    summary = {
        "issuer": discovery["issuer"],
        "jwks_uri": discovery["jwks_uri"],
        "key_count": len(jwks.get("keys", [])),
    }
    record_step(
        state,
        STEPS,
        "fetch-discovery",
        summary=summary,
        artifacts=summary,
    )
    return summary


def call_consumer_step(state: dict) -> dict:
    require_step_dependencies(state, STEPS, "call-consumer")
    jwt_token = step_artifacts(state, "mint-jwt")["jwt_token"]

    response = _json_output(
        "Authorized call to the relationship insights API",
        f"""kubectl exec -n {KUBERNETES_NAMESPACE} {POD_NAME} -- bash -lc '
curl --silent --show-error --fail \
  --header "Authorization: Bearer {jwt_token}" \
  http://jwt-consumer.{KUBERNETES_NAMESPACE}.svc.cluster.local:8080/api/relationship-insights | jq -c .
'""",
    )
    summary = {
        "message": response["message"],
        "validated_claims": response["validated_claims"],
        "insight_count": len(response["insights"]),
        "next_best_action": response["next_best_action"]["title"],
    }
    record_step(
        state,
        STEPS,
        "call-consumer",
        summary=summary,
        prepared_payload=response,
    )
    return summary


def execute_step(state: dict, step_id: str) -> dict:
    if step_id == "kubernetes-login":
        return kubernetes_login_step(state)
    if step_id == "mint-jwt":
        return mint_jwt_step(state)
    if step_id == "fetch-discovery":
        return fetch_discovery_step(state)
    if step_id == "call-consumer":
        return call_consumer_step(state)
    raise RuntimeError(f"Unsupported k8s JWT checkpoint: {step_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Kubernetes auth to JWT checkpoints.")
    parser.add_argument(
        "command",
        nargs="?",
        default="all",
        choices=[step.id for step in STEPS] + ["all", "status", "reset"],
    )
    args = parser.parse_args()

    try:
        if args.command == "reset":
            reset_state(SCENARIO)
            print_reset(SCENARIO, str(CHECKPOINT_FILE))
            return

        if args.command == "status":
            state = load_state(SCENARIO, PERSONA, STEPS)
            print_status(state, SCRIPT_NAME)
            return

        if args.command == "all":
            reset_state(SCENARIO)
            state = load_state(SCENARIO, PERSONA, STEPS)
            for step in STEPS:
                execute_step(state, step.id)
                save_state(SCENARIO, state)
            return

        state = load_state(SCENARIO, PERSONA, STEPS)
        execute_step(state, args.command)
        save_state(SCENARIO, state)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"scenario": SCENARIO, "command": args.command, "error": str(exc)}, indent=2), file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
