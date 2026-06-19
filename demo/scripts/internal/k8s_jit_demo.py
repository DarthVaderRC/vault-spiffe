from __future__ import annotations

import argparse
import json
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
    print_highlights,
    print_info,
    print_reset,
    print_status,
    run_text_command,
    run_vault_command,
)
from hashibank_demo.vault_client import read_text

SCENARIO = "k8s-jit"
PERSONA = "relationship-assistant"
SCRIPT_NAME = "demo-k8s-jit.sh"
CHECKPOINT_FILE = scenario_state_path(SCENARIO)
DEMO_ROOT = Path("/workspace/demo")
RUNTIME_DIR = DEMO_ROOT / "runtime"
ROOT_TOKEN_FILE = RUNTIME_DIR / "hashibank-vault" / "root-token"
KUBERNETES_ROLE = "relationship-assistant"
KUBERNETES_NAMESPACE = "assistants"
POD_NAME = "relationship-assistant"
VAULT_REACHABLE_ADDR = "https://host.docker.internal:18200"
POD_TOKEN_FILE = "/var/run/secrets/kubernetes.io/serviceaccount/token"
POD_ROOT_CA = "/var/run/hashibank/roots/hashibank-root-ca.crt"
DB_ROLE = "assistant-insights-readonly"
DB_CREDS_PATH = f"database/creds/{DB_ROLE}"
POSTGRES_HOST = "postgres-hashibank"
POSTGRES_PORT = 5432
POSTGRES_DB = "hashibank"

STEPS = [
    DemoStep("kubernetes-login", "Kubernetes auth login", "issuer-auth"),
    DemoStep("broker-db-creds", "Just-in-time database credentials", "identity-artifact"),
    DemoStep("query-insights", "Relationship insights query", "business-proof"),
    DemoStep("revoke-lease", "Lease revocation proof", "trust-decision"),
]


def _json_output(title: str, command: str) -> dict:
    output = run_text_command(title, command)
    return json.loads(output)


def lease_has_expired(state: dict, step_id: str, lease_duration: int | None, *, leeway_seconds: int = 20) -> bool:
    if lease_duration is None:
        return False
    completed_at = state["steps"][step_id].get("completed_at")
    if not completed_at:
        return False
    issued_at = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
    return datetime.now(timezone.utc) >= issued_at + timedelta(seconds=int(lease_duration) - leeway_seconds)


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
    print_highlights(
        f"service_account = {summary['service_account_namespace']}/{summary['service_account_name']}",
        f"vault_policies = {', '.join(summary['vault_policies'])}",
        "The workload authenticates with its Kubernetes identity, not a static secret.",
    )
    record_step(
        state,
        STEPS,
        "kubernetes-login",
        summary=summary,
        artifacts={"client_token": auth["client_token"]},
    )
    return summary


def broker_db_creds_step(state: dict) -> dict:
    require_step_dependencies(state, STEPS, "broker-db-creds")
    root_token = read_text(ROOT_TOKEN_FILE)
    client_token = step_artifacts(state, "kubernetes-login")["client_token"]

    run_vault_command(
        "Assistant dynamic database role",
        f"vault read database/roles/{DB_ROLE}",
        token=root_token,
    )
    creds_output = run_vault_command(
        "Just-in-time Postgres credentials from Vault",
        f"vault read -format=json {DB_CREDS_PATH}",
        token=client_token,
    )
    response = json.loads(creds_output)
    summary = {
        "db_username": response["data"]["username"],
        "db_lease_id": response.get("lease_id"),
        "db_lease_duration": response.get("lease_duration"),
    }
    print_highlights(
        f"db_username = {summary['db_username']}",
        f"lease_id = {summary['db_lease_id']}",
        f"lease_duration = {summary['db_lease_duration']} seconds",
        "Vault created a brand new, short-lived Postgres user tied to this request.",
    )
    record_step(
        state,
        STEPS,
        "broker-db-creds",
        summary=summary,
        artifacts={
            "db_username": summary["db_username"],
            "db_password": response["data"]["password"],
            "db_lease_id": summary["db_lease_id"],
            "db_lease_duration": summary["db_lease_duration"],
        },
    )
    return summary


def query_insights_step(state: dict) -> dict:
    require_step_dependencies(state, STEPS, "query-insights")
    db_artifacts = step_artifacts(state, "broker-db-creds")
    if lease_has_expired(state, "broker-db-creds", db_artifacts["db_lease_duration"]):
        raise RuntimeError("Brokered DB credentials expired; rerun ./scripts/demo-k8s-jit.sh")

    rows_output = run_text_command(
        "Relationship insights query with Vault-issued Postgres credentials",
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
        cur.execute("SELECT current_user AS db_user")
        db_user = cur.fetchone()["db_user"]
        cur.execute(
            '''
            SELECT customer_mask, segment, relationship_tier, lifetime_value, primary_product, next_best_action
            FROM customer_relationships
            ORDER BY lifetime_value DESC
            LIMIT 5
            '''
        )
        rows = []
        for row in cur.fetchall():
            item = dict(row)
            item["lifetime_value"] = float(item["lifetime_value"])
            rows.append(item)
print(json.dumps({{"db_user": db_user, "rows": rows}}, indent=2))
PY
        """,
        env={
            "DB_USERNAME": db_artifacts["db_username"],
            "DB_PASSWORD": db_artifacts["db_password"],
        },
        show_command=False,
    )
    result = json.loads(rows_output)
    rows = result["rows"]
    print_highlights(
        f"connected_as = {result['db_user']}",
        f"rows_returned = {len(rows)}",
        "The query runs as the ephemeral Vault-issued user, scoped to read-only relationship data.",
    )
    summary = {
        "connected_as": result["db_user"],
        "row_count": len(rows),
    }
    record_step(
        state,
        STEPS,
        "query-insights",
        summary=summary,
        prepared_payload={"persona": PERSONA, "db_user": result["db_user"], "rows": rows},
    )
    return summary


def revoke_lease_step(state: dict) -> dict:
    require_step_dependencies(state, STEPS, "revoke-lease")
    root_token = read_text(ROOT_TOKEN_FILE)
    db_artifacts = step_artifacts(state, "broker-db-creds")
    lease_id = db_artifacts["db_lease_id"]

    run_vault_command(
        "Lease metadata before revocation",
        f"vault lease lookup {lease_id}",
        token=root_token,
    )
    run_vault_command(
        "Revoke the brokered credential lease",
        f"vault lease revoke {lease_id}",
        token=root_token,
    )

    revoked_output = run_text_command(
        "Confirm the ephemeral credential no longer works",
        f"""
        python - <<'PY'
import json
import os
from psycopg import connect

try:
    connect(
        host={json.dumps(POSTGRES_HOST)},
        port={POSTGRES_PORT},
        dbname={json.dumps(POSTGRES_DB)},
        user=os.environ["DB_USERNAME"],
        password=os.environ["DB_PASSWORD"],
        sslmode="disable",
        connect_timeout=5,
    )
    print(json.dumps({{"revoked": False, "detail": "connection unexpectedly succeeded"}}))
except Exception as exc:  # noqa: BLE001
    print(json.dumps({{"revoked": True, "detail": str(exc).splitlines()[0]}}))
PY
        """,
        env={
            "DB_USERNAME": db_artifacts["db_username"],
            "DB_PASSWORD": db_artifacts["db_password"],
        },
        show_command=False,
    )
    revoked = json.loads(revoked_output)
    if not revoked.get("revoked"):
        raise RuntimeError("Expected the revoked credential to fail, but the connection succeeded")
    print_highlights(
        f"db_username = {db_artifacts['db_username']}",
        "revoked = true",
        f"detail = {revoked.get('detail')}",
        "Vault revoked the Postgres user; the leaked credential is now useless.",
    )
    print_info("Just-in-time identity proven: ephemeral user issued, used, and revoked on demand.")
    summary = {
        "db_username": db_artifacts["db_username"],
        "revoked": True,
        "detail": revoked.get("detail"),
    }
    record_step(
        state,
        STEPS,
        "revoke-lease",
        summary=summary,
        prepared_payload=summary,
    )
    return summary


def execute_step(state: dict, step_id: str) -> dict:
    if step_id == "kubernetes-login":
        return kubernetes_login_step(state)
    if step_id == "broker-db-creds":
        return broker_db_creds_step(state)
    if step_id == "query-insights":
        return query_insights_step(state)
    if step_id == "revoke-lease":
        return revoke_lease_step(state)
    raise RuntimeError(f"Unsupported k8s JIT checkpoint: {step_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Kubernetes auth to just-in-time database checkpoints.")
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
