from __future__ import annotations

import json
import os
import ssl
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cryptography import x509
from cryptography.x509.oid import ExtensionOID


PORT = int(os.environ.get("PORT", "8443"))
VAULT_ADDR = os.environ["VAULT_ADDR"]
VAULT_CA_FILE = os.environ["VAULT_CA_FILE"]
VAULT_K8S_ROLE = os.environ["VAULT_K8S_ROLE"]
VAULT_PKI_ROLE = os.environ["VAULT_PKI_ROLE"]
SERVICE_ACCOUNT_TOKEN_FILE = os.environ.get("SERVICE_ACCOUNT_TOKEN_FILE", "/var/run/secrets/kubernetes.io/serviceaccount/token")
WORKLOAD_SPIFFE_ID = os.environ["WORKLOAD_SPIFFE_ID"]
ALLOWED_CLIENT_SPIFFE_IDS = frozenset(
    entry.strip() for entry in os.environ.get("ALLOWED_CLIENT_SPIFFE_IDS", "").split(",") if entry.strip()
)
CERT_TTL = os.environ.get("CERT_TTL", "8h")
COMMON_NAME = os.environ.get("COMMON_NAME", "mtls-backend.payments.svc.cluster.local")
SERVER_CERT_FILE = os.environ["SERVER_CERT_FILE"]
SERVER_KEY_FILE = os.environ["SERVER_KEY_FILE"]
CLIENT_CA_FILE = os.environ["CLIENT_CA_FILE"]

PAYMENT_STATUS = {
    "payment_reference": "PMT-104982",
    "status": "READY_FOR_SETTLEMENT",
    "rail": "SEPA_INSTANT",
    "amount": 18250.55,
    "currency": "USD",
    "beneficiary": "Northbridge Treasury Services",
    "settlement_window": "T+0",
}


def _vault_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=VAULT_CA_FILE)


def _vault_write(path: str, payload: dict[str, object], *, token: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Vault-Token"] = token
    request = Request(
        f"{VAULT_ADDR}/v1/{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, context=_vault_context(), timeout=20) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:  # pragma: no cover - surfaced directly in startup failures
        raise RuntimeError(f"Vault write {path} failed: {exc.read().decode('utf-8')}") from exc
    except URLError as exc:  # pragma: no cover - surfaced directly in startup failures
        raise RuntimeError(f"Vault write {path} failed: {exc.reason}") from exc


def _issue_server_identity() -> None:
    token_path = Path(SERVICE_ACCOUNT_TOKEN_FILE)
    if not token_path.exists():
        raise RuntimeError(f"service account token file not found: {SERVICE_ACCOUNT_TOKEN_FILE}")

    login_response = _vault_write(
        "auth/kubernetes/login",
        {"role": VAULT_K8S_ROLE, "jwt": token_path.read_text(encoding="utf-8").strip()},
    )
    client_token = login_response["auth"]["client_token"]
    issue_response = _vault_write(
        f"pki/issue/{VAULT_PKI_ROLE}",
        {
            "common_name": COMMON_NAME,
            "uri_sans": WORKLOAD_SPIFFE_ID,
            "ttl": CERT_TTL,
        },
        token=client_token,
    )
    cert_path = Path(SERVER_CERT_FILE)
    key_path = Path(SERVER_KEY_FILE)
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    cert_path.write_text(issue_response["data"]["certificate"], encoding="utf-8")
    key_path.write_text(issue_response["data"]["private_key"], encoding="utf-8")


def _uri_sans_from_cert(peer_der: bytes) -> list[str]:
    certificate = x509.load_der_x509_certificate(peer_der)
    try:
        extension = certificate.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
    except x509.ExtensionNotFound:
        return []
    return list(extension.value.get_values_for_type(x509.UniformResourceIdentifier))


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/api/payments/status":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        peer_der = self.connection.getpeercert(binary_form=True)
        if not peer_der:
            self.send_error(HTTPStatus.UNAUTHORIZED, "client certificate required")
            return
        peer_uri_sans = _uri_sans_from_cert(peer_der)
        authorized_peer = next((spiffe_id for spiffe_id in peer_uri_sans if spiffe_id in ALLOWED_CLIENT_SPIFFE_IDS), None)
        if authorized_peer is None:
            self.send_error(HTTPStatus.FORBIDDEN, "client SPIFFE ID not authorized")
            return

        response = {
            "message": "Zero-trust mTLS peer authorized",
            "server": {
                "spiffe_id": WORKLOAD_SPIFFE_ID,
            },
            "authorized_peer": {
                "spiffe_id": authorized_peer,
                "uri_sans": peer_uri_sans,
            },
            "payment_status": PAYMENT_STATUS,
        }
        body = json.dumps(response).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return


def main() -> None:
    _issue_server_identity()
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(certfile=SERVER_CERT_FILE, keyfile=SERVER_KEY_FILE)
    context.load_verify_locations(cafile=CLIENT_CA_FILE)
    context.verify_mode = ssl.CERT_REQUIRED
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
