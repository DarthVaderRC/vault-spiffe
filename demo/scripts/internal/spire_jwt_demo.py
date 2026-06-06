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
    decode_unverified_jwt,
    jwt_has_expired,
    read_text,
    read_vault_path,
    spiffe_login_jwt,
)

SCENARIO = "spire-jwt"
PERSONA = "vault-spire-client"
SCRIPT_NAME = "demo-spire-jwt.sh"
PAGE_URL = os.environ.get("FRAUD_WEB_URL", "http://localhost:18081/")

DEMO_ROOT = Path("/workspace/demo")
RUNTIME_DIR = DEMO_ROOT / "runtime"
TLS_DIR = DEMO_ROOT / "config" / "tls"
VAULT_ADDR = "https://hashibank-vault:8200"
CA_CERT = TLS_DIR / "hashibank-root-ca.crt"
ROOT_TOKEN_FILE = RUNTIME_DIR / "hashibank-vault" / "root-token"
SPIRE_AGENT_SOCKET_PATH = "/run/spire/agent/public/api.sock"
SPIFFE_AUTH_PATH = "spire-jwt"
SPIFFE_ROLE = "vault-spire-client"
SPIFFE_AUDIENCE = "vault-spire-demo"
DB_CREDS_PATH = "database/creds/fraud-readonly"
POSTGRES_HOST = "postgres-hashibank"
POSTGRES_PORT = 5432
POSTGRES_DB = "hashibank"

STEPS = [
    DemoStep("fetch-jwt", "JWT-SVID fetch", "identity-artifact"),
    DemoStep("spiffe-jwt-auth", "SPIFFE JWT auth", "trust-decision"),
    DemoStep("db-creds", "DB creds fetch", "business-proof"),
    DemoStep("final-reveal", "Fraud data reveal", "final-reveal-prep"),
]


def lease_has_expired(state: dict, step_id: str, lease_duration: int | None, *, leeway_seconds: int = 30) -> bool:
    if lease_duration is None:
        return False
    completed_at = state["steps"][step_id].get("completed_at")
    if not completed_at:
        return False
    issued_at = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
    return datetime.now(timezone.utc) >= issued_at + timedelta(seconds=int(lease_duration) - leeway_seconds)


def _extract_spire_jwt(fetch_output: str) -> str:
    payload = json.loads(fetch_output)
    candidates = payload if isinstance(payload, list) else [payload]
    for item in candidates:
        if not isinstance(item, dict):
            continue
        svids = item.get("svids")
        if isinstance(svids, list) and svids and isinstance(svids[0], dict):
            token = svids[0].get("svid")
            if token:
                return token
    raise RuntimeError("SPIRE JWT fetch did not return a JWT-SVID")


def identity_artifact_step(state: dict) -> dict:
    fetch_output = run_text_command(
        "SPIRE JWT-SVID fetch",
        f"spire-agent api fetch jwt -socketPath {SPIRE_AGENT_SOCKET_PATH} -audience {SPIFFE_AUDIENCE} -output json",
    )
    jwt_token = _extract_spire_jwt(fetch_output)
    claims = decode_unverified_jwt(jwt_token)
    run_text_command(
        "Decoded SPIRE JWT-SVID claims",
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
    print_highlights(
        f"sub = {claims['sub']}",
        f"aud = {claims.get('aud')}",
        f"iss = {claims.get('iss')}",
    )
    summary = {
        "spiffe_subject": claims["sub"],
        "audience": claims.get("aud"),
        "issuer": claims.get("iss"),
    }
    record_step(
        state,
        STEPS,
        "fetch-jwt",
        summary=summary,
        artifacts={
            "jwt_token": jwt_token,
            "spiffe_subject": summary["spiffe_subject"],
            "issuer": summary["issuer"],
        },
    )
    return summary


def trust_decision_step(state: dict) -> dict:
    require_step_dependencies(state, STEPS, "spiffe-jwt-auth")
    jwt_artifacts = state["steps"]["fetch-jwt"]["artifacts"]
    if jwt_has_expired(jwt_artifacts["jwt_token"], leeway_seconds=30):
        raise RuntimeError("Saved JWT-SVID expired; rerun ./scripts/demo-spire-jwt.sh")

    root_token = read_text(ROOT_TOKEN_FILE)
    jwt_token = jwt_artifacts["jwt_token"]

    run_vault_command(
        "Vault SPIRE JWT auth configuration",
        f"vault read auth/{SPIFFE_AUTH_PATH}/config",
        token=root_token,
    )
    run_vault_command(
        "Vault SPIRE JWT auth role",
        f"vault read auth/{SPIFFE_AUTH_PATH}/role/{SPIFFE_ROLE}",
        token=root_token,
    )
    run_vault_command(
        "Vault SPIRE JWT login",
        f'vault write -header="Authorization=Bearer $JWT_TOKEN" auth/{SPIFFE_AUTH_PATH}/login role={SPIFFE_ROLE} type=jwt',
        env={"JWT_TOKEN": jwt_token},
    )
    access_auth = spiffe_login_jwt(
        VAULT_ADDR,
        str(CA_CERT),
        mount_path=SPIFFE_AUTH_PATH,
        role=SPIFFE_ROLE,
        svid=jwt_token,
    )
    vault_display_name = access_auth.get("display_name") or access_auth.get("metadata", {}).get("role_name")
    print_highlights(
        f"auth.display_name = {vault_display_name}",
        f"auth.policies = {', '.join(access_auth.get('policies', []))}",
        "Vault accepts the SPIRE-issued JWT-SVID and returns a token scoped for database brokering.",
    )
    summary = {
        "vault_display_name": vault_display_name,
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


def db_creds_step(state: dict) -> dict:
    require_step_dependencies(state, STEPS, "db-creds")
    root_token = read_text(ROOT_TOKEN_FILE)
    access_token = state["steps"]["spiffe-jwt-auth"]["artifacts"]["client_token"]

    run_vault_command(
        "Fraud readonly database role",
        "vault read database/roles/fraud-readonly",
        token=root_token,
    )
    run_vault_command(
        "Dynamic Postgres credentials from Vault",
        f"vault read {DB_CREDS_PATH}",
        token=access_token,
    )
    response = read_vault_path(VAULT_ADDR, str(CA_CERT), access_token, DB_CREDS_PATH)
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
    jwt_artifacts = state["steps"]["fetch-jwt"]["artifacts"]
    access_artifacts = state["steps"]["spiffe-jwt-auth"]["artifacts"]
    db_artifacts = state["steps"]["db-creds"]["artifacts"]
    if lease_has_expired(state, "db-creds", db_artifacts["db_lease_duration"]):
        raise RuntimeError("Saved DB credentials expired; rerun ./scripts/demo-spire-jwt.sh")

    rows_output = run_text_command(
        "Fraud alerts query with Vault-issued Postgres credentials",
        f"""
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
        print(json.dumps(rows, indent=2))
PY
        """,
        env={
            "DB_USERNAME": db_artifacts["db_username"],
            "DB_PASSWORD": db_artifacts["db_password"],
        },
        show_command=False,
    )
    rows = json.loads(rows_output)

    payload = {
        "persona": PERSONA,
        "spiffe_subject": jwt_artifacts["spiffe_subject"],
        "issuer": jwt_artifacts["issuer"],
        "vault_display_name": access_artifacts["vault_display_name"],
        "vault_policies": access_artifacts["vault_policies"],
        "db_username": db_artifacts["db_username"],
        "db_lease_id": db_artifacts["db_lease_id"],
        "db_lease_duration": db_artifacts["db_lease_duration"],
        "rows": rows,
    }
    print_highlights(
        f"Rendered rows = {len(rows)}",
        f"Fraud dashboard URL = {PAGE_URL}",
        "SPIRE identity becomes a Vault token, then a short-lived Postgres login, then visible fraud data.",
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
    if step_id == "fetch-jwt":
        return identity_artifact_step(state)
    if step_id == "spiffe-jwt-auth":
        return trust_decision_step(state)
    if step_id == "db-creds":
        return db_creds_step(state)
    if step_id == "final-reveal":
        return final_reveal_step(state)
    raise RuntimeError(f"Unsupported SPIRE JWT checkpoint: {step_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run interactive SPIRE JWT checkpoints.")
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
            print_reset(SCENARIO, "runtime/checkpoints/spire-jwt.json", extra_lines=[f"Page URL: {PAGE_URL}"])
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
