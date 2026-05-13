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
    run_text_command,
    run_vault_command,
)
from hashibank_demo.vault_client import (
    approle_login,
    decode_unverified_jwt,
    fetch_oidc_configuration,
    jwt_has_expired,
    mint_spiffe_jwt,
    read_text,
)

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
    root_token = read_text(ROOT_TOKEN_FILE)
    role_id = read_text(ROLE_ID_FILE)
    secret_id = read_text(SECRET_ID_FILE)

    run_vault_command(
        "Assistant AppRole role definition",
        "vault read auth/approle/role/relationship-assistant",
        token=root_token,
    )
    run_vault_command(
        "Assistant AppRole login",
        'vault write auth/approle/login role_id="$ROLE_ID" secret_id="$SECRET_ID"',
        env={"ROLE_ID": role_id, "SECRET_ID": secret_id},
    )
    issuer_auth = approle_login(VAULT_ADDR, str(CA_CERT), role_id, secret_id)
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
    root_token = read_text(ROOT_TOKEN_FILE)
    issuer_token = step_artifacts(state, "approle-login")["client_token"]

    run_vault_command(
        "Assistant SPIFFE role definition",
        f"vault read spiffe/role/{SPIFFE_ROLE}",
        token=root_token,
    )
    run_vault_command(
        "Assistant JWT-SVID mint response",
        f"vault write spiffe/role/{SPIFFE_ROLE}/mintjwt audience={SPIFFE_AUDIENCE}",
        token=issuer_token,
    )
    jwt_token, _ = mint_spiffe_jwt(VAULT_ADDR, str(CA_CERT), issuer_token, SPIFFE_ROLE, SPIFFE_AUDIENCE)
    run_text_command(
        "Decoded assistant JWT-SVID claims",
        """python - <<'PY'
import json
import os
from hashibank_demo.vault_client import decode_unverified_jwt

print(json.dumps(decode_unverified_jwt(os.environ["JWT_TOKEN"]), indent=2))
PY""",
        env={
            "JWT_TOKEN": jwt_token,
            "PYTHONPATH": "/workspace/demo/python",
        },
        show_command=False,
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
    run_vault_command(
        "OIDC discovery document for the SPIFFE engine",
        "vault read spiffe/.well-known/openid-configuration",
    )
    discovery_data = fetch_oidc_configuration(VAULT_ADDR, str(CA_CERT))
    jwks_uri = discovery_data["jwks_uri"]
    run_vault_command(
        "JWKS for assistant token validation",
        "vault read spiffe/.well-known/keys",
    )
    print_highlights(
        f"issuer = {discovery_data['issuer']}",
        f"jwks_uri = {jwks_uri}",
        "A downstream service can validate the JWT without Vault-native auth semantics.",
    )
    summary = {
        "issuer": discovery_data["issuer"],
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
        raise RuntimeError("Saved JWT-SVID expired; rerun ./scripts/demo-agentic-oidc.sh")

    validated_output = run_text_command(
        "Assistant JWT validation against discovery and JWKS",
        """python - <<'PY'
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
print(json.dumps(claims, indent=2))
PY
        """,
        env={
            "JWT_TOKEN": jwt_artifacts["jwt_token"],
            "ISSUER": discovery_artifacts["issuer"],
            "JWKS_URI": discovery_artifacts["jwks_uri"],
            "CA_CERT": str(CA_CERT),
            "AUDIENCE": SPIFFE_AUDIENCE,
            "PYTHONPATH": "/workspace/demo/python",
        },
        show_command=False,
    )
    validated_claims = json.loads(validated_output)
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
