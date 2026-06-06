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
from hashibank_demo.vault_client import extract_uri_sans, read_text


SCENARIO = "k8s-mtls"
PERSONA = "payments-api"
SCRIPT_NAME = "demo-k8s-mtls.sh"
CHECKPOINT_FILE = scenario_state_path(SCENARIO)
DEMO_ROOT = Path("/workspace/demo")
RUNTIME_DIR = DEMO_ROOT / "runtime"
ROOT_TOKEN_FILE = RUNTIME_DIR / "hashibank-vault" / "root-token"
KUBERNETES_ROLE = "payments-api"
BACKEND_KUBERNETES_ROLE = "mtls-backend"
KUBERNETES_NAMESPACE = "payments"
POD_NAME = "payments-api"
BACKEND_LABEL = "app=mtls-backend"
VAULT_REACHABLE_ADDR = "https://host.docker.internal:18200"
POD_TOKEN_FILE = "/var/run/secrets/kubernetes.io/serviceaccount/token"
POD_ROOT_CA = "/var/run/hashibank/roots/hashibank-root-ca.crt"
POD_SPIFFE_ROOT_CA = "/var/run/hashibank/spiffe/hashibank-spiffe-root.pem"
POD_CERT_FILE = "/tmp/hashibank-demo/payments.crt"
POD_KEY_FILE = "/tmp/hashibank-demo/payments.key"
FRONTEND_SPIFFE_ID = "spiffe://hashibank.demo/ns/payments/sa/payments-api"
BACKEND_SPIFFE_ID = "spiffe://hashibank.demo/ns/payments/sa/mtls-backend"

STEPS = [
    DemoStep("kubernetes-login", "Kubernetes auth login", "issuer-auth"),
    DemoStep("issue-certificate", "Frontend X.509-SVID issue", "identity-artifact"),
    DemoStep("inspect-backend-identity", "Backend X.509-SVID inspection", "identity-artifact"),
    DemoStep("mtls-request", "Zero-trust mTLS API request", "business-proof"),
]


def _kubectl_json(title: str, command: str) -> dict:
    output = run_text_command(title, command)
    return json.loads(output)


def kubernetes_login_step(state: dict) -> dict:
    root_token = read_text(ROOT_TOKEN_FILE)

    run_vault_command(
        "Payments Kubernetes auth role",
        f"vault read auth/kubernetes/role/{KUBERNETES_ROLE}",
        token=root_token,
    )
    run_text_command(
        "Payments service account",
        f"kubectl get serviceaccount -n {KUBERNETES_NAMESPACE} {KUBERNETES_ROLE} -o yaml",
    )

    login_data = _kubectl_json(
        "Payments Kubernetes auth login",
        f"""kubectl exec -n {KUBERNETES_NAMESPACE} {POD_NAME} -- bash -lc '
JWT=$(cat {POD_TOKEN_FILE})
payload=$(jq -nc --arg role {json.dumps(KUBERNETES_ROLE)} --arg jwt "$JWT" "{{\\\"role\\\": \\$role, \\\"jwt\\\": \\$jwt}}")
curl --silent --show-error --fail \\
  --cacert {POD_ROOT_CA} \\
  --header "Content-Type: application/json" \\
  --request POST \\
  --data "$payload" \\
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


def issue_certificate_step(state: dict) -> dict:
    require_step_dependencies(state, STEPS, "issue-certificate")
    root_token = read_text(ROOT_TOKEN_FILE)
    issuer_token = step_artifacts(state, "kubernetes-login")["client_token"]

    run_vault_command(
        "Payments PKI role for Kubernetes workloads",
        "vault read pki/roles/payments-k8s-spiffe",
        token=root_token,
    )

    issue_data = _kubectl_json(
        "Payments frontend certificate issue response",
        f"""kubectl exec -n {KUBERNETES_NAMESPACE} {POD_NAME} -- bash -lc '
mkdir -p /tmp/hashibank-demo
payload=$(jq -nc \\
  --arg common_name "payments-api.hashibank.demo" \\
  --arg uri_sans "{FRONTEND_SPIFFE_ID}" \\
  --arg ttl "8h" \\
  "{{\\\"common_name\\\": \\$common_name, \\\"uri_sans\\\": \\$uri_sans, \\\"ttl\\\": \\$ttl}}")
response=$(curl --silent --show-error --fail \\
  --cacert {POD_ROOT_CA} \\
  --header "Content-Type: application/json" \\
  --header "X-Vault-Token: {issuer_token}" \\
  --request POST \\
  --data "$payload" \\
  {VAULT_REACHABLE_ADDR}/v1/pki/issue/payments-k8s-spiffe)
printf "%s" "$response" | jq -r ".data.certificate" > {POD_CERT_FILE}
printf "%s" "$response" | jq -r ".data.private_key" > {POD_KEY_FILE}
printf "%s" "$response" | jq -c .
'""",
    )

    run_text_command(
        "payments-api certificate field inspection",
        f"kubectl exec -n {KUBERNETES_NAMESPACE} {POD_NAME} -- openssl x509 -noout -text -in {POD_CERT_FILE}",
    )

    uri_sans = extract_uri_sans(issue_data["data"]["certificate"])
    summary = {
        "frontend_spiffe_uri_sans": uri_sans,
        "certificate_path": POD_CERT_FILE,
        "private_key_path": POD_KEY_FILE,
    }
    record_step(
        state,
        STEPS,
        "issue-certificate",
        summary=summary,
        artifacts=summary,
    )
    return summary


def inspect_backend_identity_step(state: dict) -> dict:
    require_step_dependencies(state, STEPS, "inspect-backend-identity")
    root_token = read_text(ROOT_TOKEN_FILE)

    run_vault_command(
        "mTLS backend Kubernetes auth role",
        f"vault read auth/kubernetes/role/{BACKEND_KUBERNETES_ROLE}",
        token=root_token,
    )
    run_vault_command(
        "mTLS backend PKI role",
        "vault read pki/roles/mtls-backend-k8s-spiffe",
        token=root_token,
    )
    run_text_command(
        "mTLS backend service account",
        f"kubectl get serviceaccount -n {KUBERNETES_NAMESPACE} {BACKEND_KUBERNETES_ROLE} -o yaml",
    )
    run_text_command(
        "mTLS backend certificate field inspection",
        f"""kubectl exec -n {KUBERNETES_NAMESPACE} "$(kubectl get pod -n {KUBERNETES_NAMESPACE} -l {BACKEND_LABEL} -o jsonpath='{{.items[0].metadata.name}}')" -- \\
openssl x509 -noout -text -in /var/run/hashibank/identity/tls.crt""",
    )
    backend_cert = run_text_command(
        "mTLS backend leaf certificate",
        f"""kubectl exec -n {KUBERNETES_NAMESPACE} "$(kubectl get pod -n {KUBERNETES_NAMESPACE} -l {BACKEND_LABEL} -o jsonpath='{{.items[0].metadata.name}}')" -- \\
cat /var/run/hashibank/identity/tls.crt""",
        show_command=False,
    )
    summary = {
        "backend_spiffe_uri_sans": extract_uri_sans(backend_cert),
    }
    record_step(
        state,
        STEPS,
        "inspect-backend-identity",
        summary=summary,
        artifacts=summary,
    )
    return summary


def mtls_request_step(state: dict) -> dict:
    require_step_dependencies(state, STEPS, "mtls-request")

    response = _kubectl_json(
        "Zero-trust mTLS call from payments-api to mtls-backend",
        f"""kubectl exec -n {KUBERNETES_NAMESPACE} {POD_NAME} -- bash -lc '
python - <<PY
import json
import socket
import ssl

from cryptography import x509
from cryptography.x509.oid import ExtensionOID

expected_spiffe_id = "{BACKEND_SPIFFE_ID}"
host = "mtls-backend.{KUBERNETES_NAMESPACE}.svc.cluster.local"
request = (
    "GET /api/payments/status HTTP/1.1\\r\\n"
    "Host: " + host + "\\r\\n"
    "Connection: close\\r\\n\\r\\n"
).encode("utf-8")

context = ssl.create_default_context(cafile="{POD_SPIFFE_ROOT_CA}")
context.check_hostname = False
context.verify_mode = ssl.CERT_REQUIRED
context.load_cert_chain("{POD_CERT_FILE}", "{POD_KEY_FILE}")

with socket.create_connection((host, 8443), timeout=20) as raw_socket:
    with context.wrap_socket(raw_socket, server_hostname=host) as tls_socket:
        peer_der = tls_socket.getpeercert(binary_form=True)
        certificate = x509.load_der_x509_certificate(peer_der)
        try:
            extension = certificate.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
            uri_sans = list(extension.value.get_values_for_type(x509.UniformResourceIdentifier))
        except x509.ExtensionNotFound:
            uri_sans = []

        if expected_spiffe_id not in uri_sans:
            raise SystemExit(json.dumps(dict(error="unexpected backend SPIFFE ID", peer_uri_sans=uri_sans)))

        tls_socket.sendall(request)
        chunks = []
        while True:
            chunk = tls_socket.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)

response = b"".join(chunks)
header, body = response.split(b"\\r\\n\\r\\n", 1)
status_line = header.split(b"\\r\\n", 1)[0].decode("utf-8")
if " 200 " not in status_line:
    raise SystemExit(body.decode("utf-8"))

payload = json.loads(body.decode("utf-8"))
payload["client_verification"] = dict(
    verified_backend_spiffe_id=expected_spiffe_id,
    peer_uri_sans=uri_sans,
)
print(json.dumps(payload))
PY
'""",
    )
    summary = {
        "message": response["message"],
        "authorized_client_spiffe_id": response["authorized_peer"]["spiffe_id"],
        "verified_backend_spiffe_id": response["client_verification"]["verified_backend_spiffe_id"],
        "payment_reference": response["payment_status"]["payment_reference"],
        "payment_status": response["payment_status"]["status"],
    }
    record_step(
        state,
        STEPS,
        "mtls-request",
        summary=summary,
        prepared_payload=response,
    )
    return summary


def execute_step(state: dict, step_id: str) -> dict:
    if step_id == "kubernetes-login":
        return kubernetes_login_step(state)
    if step_id == "issue-certificate":
        return issue_certificate_step(state)
    if step_id == "inspect-backend-identity":
        return inspect_backend_identity_step(state)
    if step_id == "mtls-request":
        return mtls_request_step(state)
    raise RuntimeError(f"Unsupported k8s mTLS checkpoint: {step_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Kubernetes auth to mTLS checkpoints.")
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
