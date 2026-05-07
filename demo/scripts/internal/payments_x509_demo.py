from __future__ import annotations

import json
from pathlib import Path

from hashibank_demo.vault_client import (
    approle_login,
    extract_uri_sans,
    issue_certificate,
    read_text,
    read_vault_path,
    spiffe_login_x509,
    write_text,
)

DEMO_ROOT = Path("/workspace/demo")
RUNTIME_DIR = DEMO_ROOT / "runtime"
TLS_DIR = DEMO_ROOT / "config" / "tls"

IDENTITY_ADDR = "https://hashibank-identity:8200"
ACCESS_ADDR = "https://hashibank-access:8200"
CA_CERT = str(TLS_DIR / "hashibank-root-ca.crt")

ROLE_ID_FILE = RUNTIME_DIR / "approle" / "payments-api.role_id"
SECRET_ID_FILE = RUNTIME_DIR / "approle" / "payments-api.secret_id"
CERT_FILE = RUNTIME_DIR / "generated" / "payments-api.crt"
KEY_FILE = RUNTIME_DIR / "generated" / "payments-api.key"


def main() -> None:
    # The issuer-side Vault token is only used to request a SPIFFE-named certificate.
    issuer_auth = approle_login(
        IDENTITY_ADDR,
        CA_CERT,
        read_text(ROLE_ID_FILE),
        read_text(SECRET_ID_FILE),
    )

    cert_data = issue_certificate(
        IDENTITY_ADDR,
        CA_CERT,
        issuer_auth["client_token"],
        "payments-spiffe",
        common_name="payments-api.hashibank.demo",
        uri_sans="spiffe://hashibank.demo/payments/api",
        ttl="15m",
    )

    write_text(CERT_FILE, cert_data["certificate"])
    write_text(KEY_FILE, cert_data["private_key"])

    # The relying-party Vault cluster authenticates the workload from the client
    # certificate and returns the token that unlocks the payments proof path.
    access_auth = spiffe_login_x509(
        ACCESS_ADDR,
        CA_CERT,
        mount_path="spiffe-x509",
        role="payments-api",
        cert_file=str(CERT_FILE),
        key_file=str(KEY_FILE),
    )

    proof = read_vault_path(
        ACCESS_ADDR,
        CA_CERT,
        access_auth["client_token"],
        "kv/data/payments/bootstrap",
    )

    # Emit only the fields the walkthrough needs so the live demo stays concise.
    payload = {
        "persona": "payments-api",
        "spiffe_uri_sans": extract_uri_sans(cert_data["certificate"]),
        "vault_policies": access_auth.get("policies", []),
        "vault_display_name": access_auth.get("display_name"),
        "payments_proof": proof.get("data", {}).get("data", {}),
        "generated_files": {
            "certificate": str(CERT_FILE),
            "private_key": str(KEY_FILE),
        },
    }

    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
