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
    print_json,
    print_reset,
    print_status,
    run_captured,
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
JWT_AUDIENCE = "relationship-insights-api"

STEPS = [
    DemoStep("kubernetes-login", "Kubernetes auth login", "issuer-auth"),
    DemoStep("mint-jwt", "Vault JWT-SVID mint", "identity-artifact"),
    DemoStep("fetch-discovery", "OIDC discovery and JWKS", "trust-decision"),
    DemoStep("call-consumer", "Protected JWT consumer call", "business-proof"),
]


def _capture_json(command: str, *, env: dict | None = None) -> dict:
    return json.loads(run_captured(command, env=env))


def kubernetes_login_step(state: dict) -> dict:
    root_token = read_text(ROOT_TOKEN_FILE)

    run_vault_command(
        "Assistant Kubernetes auth role",
        f"vault read auth/kubernetes/role/{KUBERNETES_ROLE}",
        token=root_token,
    )

    login_data = _capture_json(
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
    login_command = (
        "curl --request POST \\\n"
        '  --data \'{"role": "relationship-assistant", "jwt": "$POD_JWT"}\' \\\n'
        f"  {VAULT_REACHABLE_ADDR}/v1/auth/kubernetes/login"
    )
    print_json(
        "Assistant Kubernetes auth login",
        login_data,
        command=login_command,
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

    mint_data = _capture_json(
        f"""kubectl exec -n {KUBERNETES_NAMESPACE} {POD_NAME} -- bash -lc '
payload=$(jq -nc --arg audience {json.dumps(JWT_AUDIENCE)} "{{\\\"audience\\\": \\$audience}}")
curl --silent --show-error --fail \
  --cacert {POD_ROOT_CA} \
  --header "Content-Type: application/json" \
  --header "X-Vault-Token: {issuer_token}" \
  --request POST \
  --data "$payload" \
  {VAULT_REACHABLE_ADDR}/v1/spiffe/role/{SPIFFE_ROLE}/mintjwt | jq -c .
'""",
    )
    print_json(
        "Assistant JWT-SVID mint response",
        mint_data,
        command=f"vault write spiffe/role/{SPIFFE_ROLE}/mintjwt audience={JWT_AUDIENCE}",
    )

    jwt_token = mint_data["data"]["token"]
    jwt_claims = decode_unverified_jwt(jwt_token)
    print_json("Decoded assistant JWT-SVID claims", jwt_claims)

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

    discovery = _capture_json(
        f"""curl --silent --show-error --fail \
  --cacert config/tls/hashibank-root-ca.crt \
  {VAULT_REACHABLE_ADDR}/v1/spiffe/.well-known/openid-configuration | jq -c .""",
    )
    print_json(
        "SPIFFE discovery document from Vault",
        discovery,
        command=f"curl {VAULT_REACHABLE_ADDR}/v1/spiffe/.well-known/openid-configuration",
    )

    jwks = _capture_json(
        f"""curl --silent --show-error --fail \
  --cacert config/tls/hashibank-root-ca.crt \
  {VAULT_REACHABLE_ADDR}/v1/spiffe/.well-known/keys | jq -c .""",
    )
    print_json(
        "SPIFFE JWKS from Vault",
        jwks,
        command=f"curl {VAULT_REACHABLE_ADDR}/v1/spiffe/.well-known/keys",
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

    response = _capture_json(
        f"""kubectl exec -n {KUBERNETES_NAMESPACE} {POD_NAME} -- bash -lc '
curl --silent --show-error --fail \
  --header "Authorization: Bearer {jwt_token}" \
  http://jwt-consumer.{KUBERNETES_NAMESPACE}.svc.cluster.local:8080/api/relationship-insights | jq -c .
'""",
    )
    consumer_command = (
        'curl --header "Authorization: Bearer $JWT_SVID" \\\n'
        f"  http://jwt-consumer.{KUBERNETES_NAMESPACE}.svc.cluster.local:8080/api/relationship-insights"
    )
    print_json(
        "Authorized call to the relationship insights API",
        response,
        command=consumer_command,
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
