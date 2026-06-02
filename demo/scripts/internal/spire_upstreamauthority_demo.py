from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes

from hashibank_demo.checkpoints import (
    DemoStep,
    load_state,
    record_step,
    require_step_dependencies,
    reset_state,
    save_state,
)
from hashibank_demo.transcript import (
    demo_relative_path,
    print_highlights,
    print_reset,
    print_status,
    print_step_footer,
    run_text_command,
    run_vault_command,
)
from hashibank_demo.vault_client import extract_uri_sans, read_text, read_vault_path

SCENARIO = "spire-upstreamauthority"
PERSONA = "spire-server"
SCRIPT_NAME = "demo-spire-upstreamauthority.sh"

DEMO_ROOT = Path("/workspace/demo")
RUNTIME_DIR = DEMO_ROOT / "runtime"
TLS_DIR = DEMO_ROOT / "config" / "tls"
VAULT_ADDR = "https://hashibank-vault:8200"
CA_CERT = TLS_DIR / "hashibank-root-ca.crt"
ROOT_TOKEN_FILE = RUNTIME_DIR / "hashibank-vault" / "root-token"
SPIRE_AGENT_SOCKET_PATH = "/run/spire/agent/public/api.sock"
SPIRE_UPSTREAM_ROOT_FILE = RUNTIME_DIR / "spire" / "agent" / "bootstrap" / "bootstrap-trust-bundle.pem"
GENERATED_DIR = RUNTIME_DIR / "generated" / "spire-upstreamauthority"
SVID_FILE = GENERATED_DIR / "svid.0.pem"
LEAF_FILE = GENERATED_DIR / "leaf.pem"
INTERMEDIATE_FILE = GENERATED_DIR / "intermediate.pem"

STEPS = [
    DemoStep("vault-root", "Vault SPIRE root CA", "issuer-auth"),
    DemoStep("fetch-x509", "SPIRE X.509-SVID fetch", "identity-artifact"),
    DemoStep("verify-chain", "Chain validation", "trust-decision"),
]


def _certificate_fingerprint(certificate_pem: str) -> str:
    certificate = x509.load_pem_x509_certificate(certificate_pem.encode("utf-8"))
    return certificate.fingerprint(hashes.SHA256()).hex()


def _certificate_subject(certificate_pem: str) -> str:
    certificate = x509.load_pem_x509_certificate(certificate_pem.encode("utf-8"))
    return certificate.subject.rfc4514_string()


def _split_pem_chain(chain_pem: str) -> list[str]:
    return re.findall(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", chain_pem, flags=re.DOTALL)


def issuer_auth_step(state: dict) -> dict:
    root_token = read_text(ROOT_TOKEN_FILE)
    run_vault_command(
        "Vault PKI root used by SPIRE upstreamauthority_vault",
        "vault read spire-pki/cert/ca",
        token=root_token,
    )
    response = read_vault_path(VAULT_ADDR, str(CA_CERT), root_token, "spire-pki/cert/ca")
    vault_root_pem = response["data"]["certificate"]
    bootstrap_root_pem = read_text(SPIRE_UPSTREAM_ROOT_FILE)
    vault_root_fingerprint = _certificate_fingerprint(vault_root_pem)
    bootstrap_fingerprint = _certificate_fingerprint(bootstrap_root_pem)
    if vault_root_fingerprint != bootstrap_fingerprint:
        raise RuntimeError("SPIRE bootstrap bundle does not match Vault spire-pki root certificate")
    summary = {
        "root_subject": _certificate_subject(vault_root_pem),
        "root_fingerprint": vault_root_fingerprint,
    }
    print_highlights(
        f"root_subject = {summary['root_subject']}",
        f"root_fingerprint = {summary['root_fingerprint']}",
        "SPIRE bootstraps trust from the same root certificate Vault publishes on spire-pki/cert/ca.",
    )
    record_step(
        state,
        STEPS,
        "vault-root",
        summary=summary,
        artifacts={"root_certificate_file": str(SPIRE_UPSTREAM_ROOT_FILE)},
    )
    return summary


def identity_artifact_step(state: dict) -> dict:
    require_step_dependencies(state, STEPS, "fetch-x509")
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    for path in GENERATED_DIR.glob("*"):
        path.unlink()

    run_text_command(
        "SPIRE X.509-SVID fetch",
        f"spire-agent api fetch x509 -socketPath {SPIRE_AGENT_SOCKET_PATH} -write {demo_relative_path(GENERATED_DIR)}",
    )
    chain_pem = read_text(SVID_FILE)
    certificates = _split_pem_chain(chain_pem)
    if len(certificates) < 2:
        raise RuntimeError("SPIRE X.509-SVID chain did not include the expected intermediate certificate")
    LEAF_FILE.write_text(f"{certificates[0]}\n", encoding="utf-8")
    INTERMEDIATE_FILE.write_text(f"{certificates[1]}\n", encoding="utf-8")

    run_text_command(
        "SPIRE X.509-SVID leaf certificate",
        f"openssl x509 -noout -subject -issuer -ext subjectAltName -in {demo_relative_path(LEAF_FILE)}",
    )
    run_text_command(
        "SPIRE issuing intermediate certificate",
        f"openssl x509 -noout -subject -issuer -in {demo_relative_path(INTERMEDIATE_FILE)}",
    )
    leaf_pem = read_text(LEAF_FILE)
    intermediate_pem = read_text(INTERMEDIATE_FILE)
    uri_sans = extract_uri_sans(leaf_pem)
    summary = {
        "leaf_spiffe_id": uri_sans[0] if uri_sans else "",
        "leaf_subject": _certificate_subject(leaf_pem),
        "intermediate_subject": _certificate_subject(intermediate_pem),
    }
    print_highlights(
        f"leaf_spiffe_id = {summary['leaf_spiffe_id']}",
        f"leaf_subject = {summary['leaf_subject']}",
        f"intermediate_subject = {summary['intermediate_subject']}",
    )
    record_step(
        state,
        STEPS,
        "fetch-x509",
        summary=summary,
        artifacts={
            "leaf_file": str(LEAF_FILE),
            "intermediate_file": str(INTERMEDIATE_FILE),
            "leaf_spiffe_id": summary["leaf_spiffe_id"],
        },
    )
    return summary


def trust_decision_step(state: dict) -> dict:
    require_step_dependencies(state, STEPS, "verify-chain")
    artifacts = state["steps"]["fetch-x509"]["artifacts"]
    run_text_command(
        "Validate the SPIRE workload chain against Vault's SPIRE root",
        f"openssl verify -CAfile {demo_relative_path(SPIRE_UPSTREAM_ROOT_FILE)} -untrusted {demo_relative_path(artifacts['intermediate_file'])} {demo_relative_path(artifacts['leaf_file'])}",
    )
    summary = {
        "verified_chain": True,
        "leaf_spiffe_id": artifacts["leaf_spiffe_id"],
    }
    print_highlights(
        f"leaf_spiffe_id = {summary['leaf_spiffe_id']}",
        "The SPIRE workload SVID chains back to the root certificate stored in Vault spire-pki.",
    )
    record_step(
        state,
        STEPS,
        "verify-chain",
        summary=summary,
        artifacts=summary,
    )
    return summary


def execute_step(state: dict, step_id: str) -> dict:
    if step_id == "vault-root":
        return issuer_auth_step(state)
    if step_id == "fetch-x509":
        return identity_artifact_step(state)
    if step_id == "verify-chain":
        return trust_decision_step(state)
    raise RuntimeError(f"Unsupported SPIRE upstreamauthority checkpoint: {step_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run interactive SPIRE upstream authority checkpoints.")
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
            print_reset(SCENARIO, "runtime/checkpoints/spire-upstreamauthority.json")
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
