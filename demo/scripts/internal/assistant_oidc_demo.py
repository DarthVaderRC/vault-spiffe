from __future__ import annotations

import argparse
import json
import os
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
    print_highlights,
    print_info,
    print_reset,
    print_status,
    print_step_footer,
    run_json_command,
    run_text_command,
    shell_quote,
)
from hashibank_demo.vault_client import decode_unverified_jwt, jwt_has_expired

SCENARIO = "assistant"
PERSONA = "relationship-assistant"
SCRIPT_NAME = "demo-agentic-oidc.sh"
PAGE_URL = os.environ.get("ASSISTANT_WEB_URL", "http://localhost:18082/")

DEMO_ROOT = Path("/workspace/demo")
RUNTIME_DIR = DEMO_ROOT / "runtime"
TLS_DIR = DEMO_ROOT / "config" / "tls"

VAULT_ADDR = "https://hashibank-vault:8200"
CA_CERT = TLS_DIR / "hashibank-root-ca.crt"
ROOT_TOKEN_FILE = RUNTIME_DIR / "hashibank-vault" / "root-token"
ROLE_ID_FILE = RUNTIME_DIR / "approle" / "relationship-assistant.role_id"
SECRET_ID_FILE = RUNTIME_DIR / "approle" / "relationship-assistant.secret_id"
CHECKPOINT_FILE = scenario_state_path(SCENARIO)
SPIFFE_ROLE = "relationship-assistant"
SPIFFE_AUDIENCE = "assistant-ui"

MASKED_CONTEXT = [
    {
        "relationship_manager": "S. Chen",
        "customer": "A**** M******",
        "segment": "Premier Retail",
        "context": "Mortgage refinance inquiry with high savings balance",
    },
    {
        "relationship_manager": "T. Singh",
        "customer": "R*** L**",
        "segment": "Business Banking",
        "context": "Treasury onboarding follow-up with pending KYC document review",
    },
    {
        "relationship_manager": "M. Alvarez",
        "customer": "J*** P******",
        "segment": "Private Banking",
        "context": "Portfolio review request after large outbound transfer",
    },
]

STEPS = [
    DemoStep("approle-login", "AppRole login", "issuer-auth"),
    DemoStep("mint-jwt", "JWT-SVID mint", "identity-artifact"),
    DemoStep("fetch-discovery", "Discovery fetch", "trust-decision"),
    DemoStep("validate-jwt", "JWT validation", "trust-decision"),
    DemoStep("final-reveal", "Final page reveal", "final-reveal-prep"),
]


def issuer_auth_step(state: dict) -> dict:
    run_json_command(
        "Assistant AppRole role definition",
        f"""
        ROOT_TOKEN=$(cat {shell_quote(ROOT_TOKEN_FILE)})
        curl --silent --show-error --fail \
          --cacert {shell_quote(CA_CERT)} \
          --header "X-Vault-Token: $ROOT_TOKEN" \
          {shell_quote(f"{VAULT_ADDR}/v1/auth/approle/role/relationship-assistant")}
        """,
    )
    response = run_json_command(
        "Assistant AppRole login",
        f"""
        ROLE_ID=$(cat {shell_quote(ROLE_ID_FILE)})
        SECRET_ID=$(cat {shell_quote(SECRET_ID_FILE)})
        curl --silent --show-error --fail \
          --cacert {shell_quote(CA_CERT)} \
          --header "Content-Type: application/json" \
          --request POST \
          --data "$(jq -nc --arg role_id "$ROLE_ID" --arg secret_id "$SECRET_ID" '{{role_id:$role_id, secret_id:$secret_id}}')" \
          {shell_quote(f"{VAULT_ADDR}/v1/auth/approle/login")}
        """,
    )
    issuer_auth = response["auth"]
    print_highlights(
        "Check the AppRole role output for alias metadata such as spiffe_path and line_of_business.",
        f"auth.metadata.role_name = {issuer_auth.get('metadata', {}).get('role_name')}",
        f"auth.client_token is the issuer-side Vault token for {PERSONA}",
    )
    summary = {
        "vault_policies": issuer_auth.get("policies", []),
        "vault_token_type": issuer_auth.get("token_type"),
        "vault_lease_duration": issuer_auth.get("lease_duration"),
    }
    record_step(
        state,
        STEPS,
        "approle-login",
        summary=summary,
        artifacts={"client_token": issuer_auth["client_token"]},
    )
    return summary


def identity_artifact_step(state: dict) -> dict:
    require_step_dependencies(state, STEPS, "mint-jwt")
    run_json_command(
        "Assistant SPIFFE role definition",
        f"""
        ROOT_TOKEN=$(cat {shell_quote(ROOT_TOKEN_FILE)})
        curl --silent --show-error --fail \
          --cacert {shell_quote(CA_CERT)} \
          --header "X-Vault-Token: $ROOT_TOKEN" \
          {shell_quote(f"{VAULT_ADDR}/v1/spiffe/role/{SPIFFE_ROLE}")}
        """,
    )
    response = run_json_command(
        "Assistant JWT-SVID mint response",
        f"""
        ISSUER_TOKEN=$(jq -r '.steps["approle-login"].artifacts.client_token' {shell_quote(CHECKPOINT_FILE)})
        curl --silent --show-error --fail \
          --cacert {shell_quote(CA_CERT)} \
          --header "X-Vault-Token: $ISSUER_TOKEN" \
          --header "Content-Type: application/json" \
          --request POST \
          --data "$(jq -nc --arg audience {shell_quote(SPIFFE_AUDIENCE)} '{{audience:$audience}}')" \
          {shell_quote(f"{VAULT_ADDR}/v1/spiffe/role/{SPIFFE_ROLE}/mintjwt")}
        """,
    )
    jwt_token = response["data"]["token"]
    run_text_command(
        "Raw assistant JWT-SVID",
        f"printf '%s\\n' {shell_quote(jwt_token)}",
    )
    jwt_claims = decode_unverified_jwt(jwt_token)
    print_highlights(
        f"sub = {jwt_claims['sub']}",
        f"aud = {jwt_claims.get('aud')}",
        "This token is meant for downstream OIDC-style validation.",
    )
    summary = {
        "spiffe_subject": jwt_claims["sub"],
        "audience": jwt_claims.get("aud"),
    }
    record_step(
        state,
        STEPS,
        "mint-jwt",
        summary=summary,
        artifacts={"jwt_token": jwt_token, "spiffe_subject": summary["spiffe_subject"]},
    )
    return summary


def discovery_step(state: dict) -> dict:
    require_step_dependencies(state, STEPS, "fetch-discovery")
    discovery = run_json_command(
        "OIDC discovery document for the SPIFFE engine",
        f"""
        curl --silent --show-error --fail \
          --cacert {shell_quote(CA_CERT)} \
          {shell_quote(f"{VAULT_ADDR}/v1/spiffe/.well-known/openid-configuration")}
        """,
    )
    jwks_uri = discovery["jwks_uri"]
    run_json_command(
        "JWKS for assistant token validation",
        f"""
        curl --silent --show-error --fail \
          --cacert {shell_quote(CA_CERT)} \
          {shell_quote(jwks_uri)}
        """,
    )
    print_highlights(
        f"issuer = {discovery['issuer']}",
        f"jwks_uri = {jwks_uri}",
        "A downstream service can validate the JWT without Vault-native auth semantics.",
    )
    summary = {
        "issuer": discovery["issuer"],
        "jwks_uri": jwks_uri,
    }
    record_step(
        state,
        STEPS,
        "fetch-discovery",
        summary=summary,
        artifacts=summary,
    )
    return summary


def trust_decision_step(state: dict) -> dict:
    require_step_dependencies(state, STEPS, "validate-jwt")
    jwt_artifacts = step_artifacts(state, "mint-jwt")
    discovery_artifacts = step_artifacts(state, "fetch-discovery")
    if jwt_has_expired(jwt_artifacts["jwt_token"], leeway_seconds=30):
        raise RuntimeError("Saved JWT-SVID expired; rerun ./scripts/demo-agentic-oidc.sh mint-jwt")

    validated_claims = run_json_command(
        "Assistant JWT validation against discovery and JWKS",
        f"""
        JWT_TOKEN=$(jq -r '.steps["mint-jwt"].artifacts.jwt_token' {shell_quote(CHECKPOINT_FILE)})
        ISSUER=$(jq -r '.steps["fetch-discovery"].artifacts.issuer' {shell_quote(CHECKPOINT_FILE)})
        JWKS_URI=$(jq -r '.steps["fetch-discovery"].artifacts.jwks_uri' {shell_quote(CHECKPOINT_FILE)})
        export JWT_TOKEN ISSUER JWKS_URI
        export PYTHONPATH=/workspace/demo/python
        export CA_CERT={shell_quote(str(CA_CERT))}
        export AUDIENCE={shell_quote(SPIFFE_AUDIENCE)}
        python - <<'PY'
import json
import os
from hashibank_demo.vault_client import validate_spiffe_jwt

claims = validate_spiffe_jwt(
    os.environ["JWT_TOKEN"],
    issuer=os.environ["ISSUER"],
    audience=os.environ["AUDIENCE"],
    jwks_uri=os.environ["JWKS_URI"],
    ca_cert=os.environ["CA_CERT"],
)
print(json.dumps(claims))
PY
        """,
    )
    print_highlights(
        f"sub = {validated_claims['sub']}",
        f"iss = {validated_claims['iss']}",
        f"aud = {validated_claims['aud']}",
    )
    summary = {
        "sub": validated_claims["sub"],
        "iss": validated_claims["iss"],
        "aud": validated_claims["aud"],
        "vault_entity_id": validated_claims.get("vault", {}).get("entity", {}).get("id"),
    }
    record_step(
        state,
        STEPS,
        "validate-jwt",
        summary=summary,
        artifacts={"validated_claims": summary},
    )
    return summary


def final_reveal_step(state: dict) -> dict:
    require_step_dependencies(state, STEPS, "final-reveal")
    validated_claims = step_artifacts(state, "validate-jwt")["validated_claims"]
    payload = {
        "persona": PERSONA,
        "validated_claims": validated_claims,
        "contexts": MASKED_CONTEXT,
    }
    print_info(f"Open {PAGE_URL}")
    summary = {
        "page_ready": True,
        "page_url": PAGE_URL,
        "contexts_loaded": len(MASKED_CONTEXT),
    }
    record_step(
        state,
        STEPS,
        "final-reveal",
        summary=summary,
        prepared_payload=payload,
    )
    return summary


def execute_step(state: dict, step_id: str) -> dict:
    if step_id == "approle-login":
        return issuer_auth_step(state)
    if step_id == "mint-jwt":
        return identity_artifact_step(state)
    if step_id == "fetch-discovery":
        return discovery_step(state)
    if step_id == "validate-jwt":
        return trust_decision_step(state)
    if step_id == "final-reveal":
        return final_reveal_step(state)
    raise RuntimeError(f"Unsupported assistant checkpoint: {step_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run interactive assistant OIDC checkpoints.")
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
            print_reset(SCENARIO, "runtime/checkpoints/assistant.json", extra_lines=[f"Page URL: {PAGE_URL}"])
            return

        if args.command == "status":
            state = load_state(SCENARIO, PERSONA, STEPS)
            print_status(state, SCRIPT_NAME, extra_lines=[f"Page URL: {PAGE_URL}"])
            return

        if args.command == "all":
            reset_state(SCENARIO)
            state = load_state(SCENARIO, PERSONA, STEPS)
            for step in STEPS:
                execute_step(state, step.id)
                save_state(SCENARIO, state)
                print_step_footer(state, SCRIPT_NAME, extra_lines=[f"Page URL: {PAGE_URL}"] if step.id == "final-reveal" else None)
            return

        state = load_state(SCENARIO, PERSONA, STEPS)
        execute_step(state, args.command)
        save_state(SCENARIO, state)
        print_step_footer(state, SCRIPT_NAME, extra_lines=[f"Page URL: {PAGE_URL}"] if args.command == "final-reveal" else None)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"scenario": SCENARIO, "command": args.command, "error": str(exc)}, indent=2), file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
