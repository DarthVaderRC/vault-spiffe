from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
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
from hashibank_demo.vault_client import decode_unverified_jwt, jwt_has_expired

SCENARIO = "fraud"
PERSONA = "fraud-ops-web"
SCRIPT_NAME = "demo-jwt-fraud.sh"
PAGE_URL = os.environ.get("FRAUD_WEB_URL", "http://localhost:18081/")

DEMO_ROOT = Path("/workspace/demo")
RUNTIME_DIR = DEMO_ROOT / "runtime"
ROLE_ID_FILE = RUNTIME_DIR / "approle" / "fraud-ops-web.role_id"
SECRET_ID_FILE = RUNTIME_DIR / "approle" / "fraud-ops-web.secret_id"
CHECKPOINT_FILE = scenario_state_path(SCENARIO)
ROLE_ID_REF = demo_relative_path(ROLE_ID_FILE)
SECRET_ID_REF = demo_relative_path(SECRET_ID_FILE)
CHECKPOINT_REF = demo_relative_path(CHECKPOINT_FILE)
SPIFFE_ROLE = "fraud-ops-web"
SPIFFE_AUDIENCE = "hashibank-vault"
SPIFFE_AUTH_PATH = "spiffe-jwt"
DB_CREDS_PATH = "database/creds/fraud-readonly"
POSTGRES_HOST = "postgres-hashibank"
POSTGRES_PORT = 5432
POSTGRES_DB = "hashibank"

STEPS = [
    DemoStep("approle-login", "AppRole login", "issuer-auth"),
    DemoStep("mint-jwt", "JWT-SVID mint", "identity-artifact"),
    DemoStep("spiffe-jwt-auth", "SPIFFE JWT auth", "trust-decision"),
    DemoStep("db-creds", "DB creds fetch", "business-proof"),
    DemoStep("final-reveal", "Final page reveal", "final-reveal-prep"),
]


def lease_has_expired(state: dict, step_id: str, lease_duration: int | None, *, leeway_seconds: int = 30) -> bool:
    if lease_duration is None:
        return False
    completed_at = state["steps"][step_id].get("completed_at")
    if not completed_at:
        return False
    issued_at = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
    return datetime.now(timezone.utc) >= issued_at + timedelta(seconds=int(lease_duration) - leeway_seconds)


def issuer_auth_step(state: dict) -> dict:
    run_json_command(
        "Fraud AppRole role definition",
        vault_cli_command(
            "vault read -format=json auth/approle/role/fraud-ops-web",
            root_token=True,
        ),
    )
    response = run_json_command(
        "Fraud AppRole login",
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
        "Fraud SPIFFE role definition",
        vault_cli_command(
            f"vault read -format=json spiffe/role/{SPIFFE_ROLE}",
            root_token=True,
        ),
    )
    response = run_json_command(
        "Fraud JWT-SVID mint response",
        vault_cli_command(
            f"""
            export VAULT_TOKEN=$(jq -r '.steps["approle-login"].artifacts.client_token' {shell_quote(CHECKPOINT_REF)})
            vault write -format=json spiffe/role/{SPIFFE_ROLE}/mintjwt audience={shell_quote(SPIFFE_AUDIENCE)}
            """
        ),
    )
    jwt_token = response["data"]["token"]
    run_text_command(
        "Raw fraud JWT-SVID",
        f"printf '%s\\n' {shell_quote(jwt_token)}",
    )
    jwt_claims = decode_unverified_jwt(jwt_token)
    print_highlights(
        f"sub = {jwt_claims['sub']}",
        f"aud = {jwt_claims.get('aud')}",
        f"vault.entity.id = {jwt_claims.get('vault', {}).get('entity', {}).get('id')}",
    )
    summary = {
        "spiffe_subject": jwt_claims["sub"],
        "audience": jwt_claims.get("aud"),
        "vault_entity_id": jwt_claims.get("vault", {}).get("entity", {}).get("id"),
    }
    record_step(
        state,
        STEPS,
        "mint-jwt",
        summary=summary,
        artifacts={
            "jwt_token": jwt_token,
            "spiffe_subject": summary["spiffe_subject"],
            "vault_entity_id": summary["vault_entity_id"],
        },
    )
    return summary


def trust_decision_step(state: dict) -> dict:
    require_step_dependencies(state, STEPS, "spiffe-jwt-auth")
    jwt_artifacts = step_artifacts(state, "mint-jwt")
    if jwt_has_expired(jwt_artifacts["jwt_token"], leeway_seconds=30):
        raise RuntimeError("Saved JWT-SVID expired; rerun ./scripts/demo-jwt-fraud.sh mint-jwt")

    run_json_command(
        "SPIFFE JWT auth role for fraud-ops-web",
        vault_cli_command(
            f"vault read -format=json auth/{SPIFFE_AUTH_PATH}/role/{SPIFFE_ROLE}",
            root_token=True,
        ),
    )
    response = run_json_command(
        "SPIFFE JWT login for fraud-ops-web",
        vault_cli_command(
            f"""
            JWT_TOKEN=$(jq -r '.steps["mint-jwt"].artifacts.jwt_token' {shell_quote(CHECKPOINT_REF)})
            vault write -format=json \
              -header="Authorization=Bearer $JWT_TOKEN" \
              auth/{SPIFFE_AUTH_PATH}/login role={shell_quote(SPIFFE_ROLE)} type=jwt
            """
        ),
    )
    access_auth = response["auth"]
    print_highlights(
        f"auth.display_name = {access_auth.get('display_name')}",
        f"auth.policies = {', '.join(access_auth.get('policies', []))}",
        "This login exchanges the JWT-SVID for a Vault token on the same cluster.",
    )
    summary = {
        "vault_display_name": access_auth.get("display_name"),
        "vault_policies": access_auth.get("policies", []),
    }
    record_step(
        state,
        STEPS,
        "spiffe-jwt-auth",
        summary=summary,
        artifacts={
            "client_token": access_auth["client_token"],
            "vault_display_name": summary["vault_display_name"],
            "vault_policies": summary["vault_policies"],
        },
    )
    return summary


def business_proof_step(state: dict) -> dict:
    require_step_dependencies(state, STEPS, "db-creds")
    response = run_json_command(
        "Dynamic Postgres credentials from Vault",
        vault_cli_command(
            f"""
            export VAULT_TOKEN=$(jq -r '.steps["spiffe-jwt-auth"].artifacts.client_token' {shell_quote(CHECKPOINT_REF)})
            vault read -format=json {DB_CREDS_PATH}
            """
        ),
    )
    summary = {
        "db_username": response["data"]["username"],
        "db_lease_id": response.get("lease_id"),
        "db_lease_duration": response.get("lease_duration"),
    }
    print_highlights(
        f"db_username = {summary['db_username']}",
        f"lease_id = {summary['db_lease_id']}",
        f"lease_duration = {summary['db_lease_duration']} seconds",
    )
    record_step(
        state,
        STEPS,
        "db-creds",
        summary=summary,
        artifacts={
            "db_username": summary["db_username"],
            "db_password": response["data"]["password"],
            "db_lease_id": summary["db_lease_id"],
            "db_lease_duration": summary["db_lease_duration"],
        },
    )
    return summary


def final_reveal_step(state: dict) -> dict:
    require_step_dependencies(state, STEPS, "final-reveal")
    jwt_artifacts = step_artifacts(state, "mint-jwt")
    access_artifacts = step_artifacts(state, "spiffe-jwt-auth")
    db_artifacts = step_artifacts(state, "db-creds")
    if lease_has_expired(state, "db-creds", db_artifacts["db_lease_duration"]):
        raise RuntimeError("Saved DB credentials expired; rerun ./scripts/demo-jwt-fraud.sh db-creds")

    rows = run_json_command(
        "Fraud alerts query with Vault-issued Postgres credentials",
        f"""
        DB_USERNAME=$(jq -r '.steps["db-creds"].artifacts.db_username' {shell_quote(CHECKPOINT_REF)})
        DB_PASSWORD=$(jq -r '.steps["db-creds"].artifacts.db_password' {shell_quote(CHECKPOINT_REF)})
        export DB_USERNAME DB_PASSWORD
        python - <<'PY'
import json
import os
from psycopg import connect
from psycopg.rows import dict_row

with connect(
    host={json.dumps(POSTGRES_HOST)},
    port={POSTGRES_PORT},
    dbname={json.dumps(POSTGRES_DB)},
    user=os.environ["DB_USERNAME"],
    password=os.environ["DB_PASSWORD"],
    sslmode="disable",
    row_factory=dict_row,
) as conn:
    with conn.cursor() as cur:
        cur.execute(
            '''
            SELECT account_mask, severity, status, amount, merchant, event_time
            FROM fraud_alerts
            ORDER BY event_time DESC
            LIMIT 5
            '''
        )
        rows = []
        for row in cur.fetchall():
            item = dict(row)
            item["amount"] = float(item["amount"])
            item["event_time"] = item["event_time"].isoformat()
            rows.append(item)
        print(json.dumps(rows))
PY
        """,
    )

    payload = {
        "persona": PERSONA,
        "spiffe_subject": jwt_artifacts["spiffe_subject"],
        "vault_entity_id": jwt_artifacts["vault_entity_id"],
        "vault_policies": access_artifacts["vault_policies"],
        "db_username": db_artifacts["db_username"],
        "db_lease_id": db_artifacts["db_lease_id"],
        "db_lease_duration": db_artifacts["db_lease_duration"],
        "rows": rows,
    }
    print_highlights(
        f"Rendered rows = {len(rows)}",
        f"Fraud dashboard URL = {PAGE_URL}",
        "The page renders from prepared checkpoint state and does not rerun Vault calls on load.",
    )
    print_info(f"Open {PAGE_URL}")
    summary = {
        "page_ready": True,
        "page_url": PAGE_URL,
        "rendered_rows": len(rows),
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
    if step_id == "spiffe-jwt-auth":
        return trust_decision_step(state)
    if step_id == "db-creds":
        return business_proof_step(state)
    if step_id == "final-reveal":
        return final_reveal_step(state)
    raise RuntimeError(f"Unsupported fraud checkpoint: {step_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run interactive fraud JWT checkpoints.")
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
            print_reset(SCENARIO, "runtime/checkpoints/fraud.json", extra_lines=[f"Page URL: {PAGE_URL}"])
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
