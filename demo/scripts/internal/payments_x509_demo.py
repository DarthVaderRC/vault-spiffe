from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from hashibank_demo.checkpoints import (
    DemoStep,
    display_path,
    load_state,
    record_step,
    require_step_dependencies,
    reset_state,
    save_state,
    scenario_state_path,
    step_artifacts,
)
from hashibank_demo.transcript import (
    demo_relative_path,
    print_highlights,
    print_info,
    print_reset,
    print_status,
    print_step_footer,
    run_json_command,
    run_text_command,
    shell_quote,
    vault_cli_command,
)
from hashibank_demo.vault_client import certificate_has_expired, extract_uri_sans, read_text, write_text

DEMO_ROOT = Path("/workspace/demo")
RUNTIME_DIR = DEMO_ROOT / "runtime"
ROLE_ID_FILE = RUNTIME_DIR / "approle" / "payments-api.role_id"
SECRET_ID_FILE = RUNTIME_DIR / "approle" / "payments-api.secret_id"
CERT_FILE = RUNTIME_DIR / "generated" / "payments-api.crt"
KEY_FILE = RUNTIME_DIR / "generated" / "payments-api.key"

SCENARIO = "payments"
PERSONA = "payments-api"
SCRIPT_NAME = "demo-x509-payments.sh"
CHECKPOINT_FILE = scenario_state_path(SCENARIO)
ROLE_ID_REF = demo_relative_path(ROLE_ID_FILE)
SECRET_ID_REF = demo_relative_path(SECRET_ID_FILE)
CHECKPOINT_REF = demo_relative_path(CHECKPOINT_FILE)
CERT_REF = demo_relative_path(CERT_FILE)
KEY_REF = demo_relative_path(KEY_FILE)

STEPS = [
    DemoStep("approle-login", "AppRole login", "issuer-auth"),
    DemoStep("pki-issue", "PKI issue", "identity-artifact"),
    DemoStep("spiffe-x509-auth", "SPIFFE X.509 auth", "trust-decision"),
    DemoStep("payments-api-kv-secrets", "Payments API KV secrets", "business-proof"),
]


def issuer_auth_step(state: dict) -> dict:
    run_json_command(
        "Payments AppRole role definition",
        vault_cli_command(
            "vault read -format=json auth/approle/role/payments-api",
            root_token=True,
        ),
    )
    response = run_json_command(
        "Payments AppRole login",
        vault_cli_command(
            f"""
            ROLE_ID=$(cat {shell_quote(ROLE_ID_REF)})
            SECRET_ID=$(cat {shell_quote(SECRET_ID_REF)})
            vault write -format=json auth/approle/login role_id="$ROLE_ID" secret_id="$SECRET_ID"
            """
        ),
    )
    issuer_auth = response["auth"]
    print_highlights(
        f"auth.client_token is minted for {PERSONA}",
        f"auth.metadata.role_name = {issuer_auth.get('metadata', {}).get('role_name')}",
        f"auth.policies = {', '.join(issuer_auth.get('policies', []))}",
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
    require_step_dependencies(state, STEPS, "pki-issue")
    run_json_command(
        "Payments PKI role definition",
        vault_cli_command(
            "vault read -format=json pki/roles/payments-spiffe",
            root_token=True,
        ),
    )
    response = run_json_command(
        "Payments certificate issue request",
        vault_cli_command(
            f"""
            export VAULT_TOKEN=$(jq -r '.steps["approle-login"].artifacts.client_token' {shell_quote(CHECKPOINT_REF)})
            vault write -format=json pki/issue/payments-spiffe \
              common_name="payments-api.hashibank.demo" \
              uri_sans="spiffe://hashibank.demo/payments/api" \
              ttl=15m
            """
        ),
    )
    cert_data = response["data"]
    write_text(CERT_FILE, cert_data["certificate"])
    write_text(KEY_FILE, cert_data["private_key"])

    run_text_command(
        "Generated payments-api.crt",
        f"cat {shell_quote(CERT_REF)}",
    )
    run_text_command(
        "payments-api.crt field inspection",
        f"openssl x509 -noout -text -in {shell_quote(CERT_REF)}",
    )

    uri_sans = extract_uri_sans(cert_data["certificate"])
    print_highlights(
        f"URI SANs = {', '.join(uri_sans)}",
        f"Certificate file = {display_path(CERT_FILE)}",
        f"Private key file = {display_path(KEY_FILE)}",
    )

    summary = {
        "spiffe_uri_sans": uri_sans,
        "generated_files": {
            "certificate": display_path(CERT_FILE),
            "private_key": display_path(KEY_FILE),
        },
    }
    record_step(
        state,
        STEPS,
        "pki-issue",
        summary=summary,
        artifacts={
            "certificate_file": str(CERT_FILE),
            "key_file": str(KEY_FILE),
            "spiffe_uri_sans": uri_sans,
            "generated_files": summary["generated_files"],
        },
    )
    return summary


def trust_decision_step(state: dict) -> dict:
    require_step_dependencies(state, STEPS, "spiffe-x509-auth")
    cert_artifacts = step_artifacts(state, "pki-issue")
    if certificate_has_expired(read_text(cert_artifacts["certificate_file"]), leeway_seconds=30):
        raise RuntimeError("Saved certificate expired; rerun ./scripts/demo-x509-payments.sh pki-issue")

    run_json_command(
        "SPIFFE X.509 auth role for payments-api",
        vault_cli_command(
            "vault read -format=json auth/spiffe-x509/role/payments-api",
            root_token=True,
        ),
    )
    response = run_json_command(
        "SPIFFE X.509 login with payments certificate",
        vault_cli_command(
            f"""
            vault write -format=json \
              -client-cert={shell_quote(CERT_REF)} \
              -client-key={shell_quote(KEY_REF)} \
              auth/spiffe-x509/login role=payments-api type=cert
            """
        ),
    )
    access_auth = response["auth"]
    print_highlights(
        f"auth.display_name = {access_auth.get('display_name')}",
        f"auth.policies = {', '.join(access_auth.get('policies', []))}",
        "The payments certificate URI SAN is authorized by auth/spiffe-x509/role/payments-api.",
    )

    summary = {
        "vault_display_name": access_auth.get("display_name"),
        "vault_policies": access_auth.get("policies", []),
    }
    record_step(
        state,
        STEPS,
        "spiffe-x509-auth",
        summary=summary,
        artifacts={
            "client_token": access_auth["client_token"],
            "vault_display_name": summary["vault_display_name"],
            "vault_policies": summary["vault_policies"],
        },
    )
    return summary


def business_proof_step(state: dict) -> dict:
    require_step_dependencies(state, STEPS, "payments-api-kv-secrets")
    cert_artifacts = step_artifacts(state, "pki-issue")
    access_artifacts = step_artifacts(state, "spiffe-x509-auth")
    response = run_json_command(
        "Payments API KV secrets read",
        vault_cli_command(
            f"""
            export VAULT_TOKEN=$(jq -r '.steps["spiffe-x509-auth"].artifacts.client_token' {shell_quote(CHECKPOINT_REF)})
            vault kv get -format=json kv/payments/api-secrets
            """
        ),
    )
    payload = {
        "persona": PERSONA,
        "spiffe_uri_sans": cert_artifacts["spiffe_uri_sans"],
        "vault_policies": access_artifacts["vault_policies"],
        "vault_display_name": access_artifacts["vault_display_name"],
        "payments_api_kv_secrets": response.get("data", {}).get("data", {}),
        "generated_files": cert_artifacts["generated_files"],
    }
    print_highlights(
        f"KV path = kv/payments/api-secrets",
        f"vault_display_name = {access_artifacts['vault_display_name']}",
        f"payments_api_kv_secrets.service = {payload['payments_api_kv_secrets'].get('service')}",
    )
    record_step(
        state,
        STEPS,
        "payments-api-kv-secrets",
        summary=payload,
        prepared_payload=payload,
    )
    return payload


def execute_step(state: dict, step_id: str) -> dict:
    if step_id == "approle-login":
        return issuer_auth_step(state)
    if step_id == "pki-issue":
        return identity_artifact_step(state)
    if step_id == "spiffe-x509-auth":
        return trust_decision_step(state)
    if step_id == "payments-api-kv-secrets":
        return business_proof_step(state)
    raise RuntimeError(f"Unsupported payments checkpoint: {step_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run interactive payments X.509 checkpoints.")
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
            print_reset(SCENARIO, "runtime/checkpoints/payments.json")
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
                print_step_footer(state, SCRIPT_NAME)
            return

        state = load_state(SCENARIO, PERSONA, STEPS)
        execute_step(state, args.command)
        save_state(SCENARIO, state)
        print_step_footer(state, SCRIPT_NAME)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"scenario": SCENARIO, "command": args.command, "error": str(exc)}, indent=2), file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
