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
)
from hashibank_demo.transcript import (
    print_highlights,
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
SPIRE_KV_PATH = "kv/data/spire/demo"

STEPS = [
    DemoStep("fetch-jwt", "JWT-SVID fetch", "identity-artifact"),
    DemoStep("spiffe-jwt-auth", "SPIFFE JWT auth", "trust-decision"),
    DemoStep("kv-read", "KV read", "business-proof"),
]


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
        f"exp = {claims.get('exp')}",
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
        artifacts={"jwt_token": jwt_token},
    )
    return summary


def trust_decision_step(state: dict) -> dict:
    require_step_dependencies(state, STEPS, "spiffe-jwt-auth")
    jwt_token = state["steps"]["fetch-jwt"]["artifacts"]["jwt_token"]
    if jwt_has_expired(jwt_token, leeway_seconds=30):
        raise RuntimeError("Saved JWT-SVID expired; rerun ./scripts/demo-spire-jwt.sh")

    root_token = read_text(ROOT_TOKEN_FILE)
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
    print_highlights(
        f"auth.display_name = {access_auth.get('display_name')}",
        f"auth.policies = {', '.join(access_auth.get('policies', []))}",
        "Vault accepted the SPIRE-issued JWT-SVID using the SPIRE federation bundle.",
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
        artifacts={"client_token": access_auth["client_token"]},
    )
    return summary


def business_proof_step(state: dict) -> dict:
    require_step_dependencies(state, STEPS, "kv-read")
    client_token = state["steps"]["spiffe-jwt-auth"]["artifacts"]["client_token"]

    run_vault_command(
        "Vault SPIRE demo KV read",
        "vault kv get kv/spire/demo",
        token=client_token,
    )
    response = read_vault_path(VAULT_ADDR, str(CA_CERT), client_token, SPIRE_KV_PATH)
    data = response["data"]["data"]
    summary = {
        "message": data["message"],
        "trust_domain": data["trust_domain"],
    }
    print_highlights(
        f"trust_domain = {summary['trust_domain']}",
        f"message = {summary['message']}",
    )
    record_step(
        state,
        STEPS,
        "kv-read",
        summary=summary,
        artifacts=summary,
    )
    return summary


def execute_step(state: dict, step_id: str) -> dict:
    if step_id == "fetch-jwt":
        return identity_artifact_step(state)
    if step_id == "spiffe-jwt-auth":
        return trust_decision_step(state)
    if step_id == "kv-read":
        return business_proof_step(state)
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
            print_reset(SCENARIO, "runtime/checkpoints/spire-jwt.json")
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
