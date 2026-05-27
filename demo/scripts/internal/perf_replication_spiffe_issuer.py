from __future__ import annotations

import json
import time
from json import JSONDecodeError
from pathlib import Path
from urllib.parse import urljoin

import requests
from requests import RequestException

from hashibank_demo.vault_client import (
    VaultDemoError,
    approle_login,
    decode_unverified_jwt,
    mint_spiffe_jwt,
    read_text,
    read_vault_path,
    write_text,
)

DEMO_ROOT = Path("/workspace/demo")
RUNTIME_DIR = DEMO_ROOT / "runtime"
TLS_DIR = DEMO_ROOT / "config" / "tls"

PRIMARY_ADDR = "https://hashibank-vault-perf-primary:8200"
REPLICA_ADDR = "https://hashibank-vault-perf:8200"
PRIMARY_CLUSTER_ADDR = "https://hashibank-vault-perf-primary:8200"
REPLICA_CLUSTER_ADDR = "https://hashibank-vault-perf:8200"
CA_CERT = TLS_DIR / "hashibank-root-ca.crt"

TEST_MOUNT = "spiffe-default-issuer"
SPIFFE_ROLE = "perf-repl-spiffe-issuer"
APPROLE_NAME = "perf-repl-spiffe-issuer"
AUDIENCE = "perf-replica-issuer-check"

ROLE_ID_FILE = RUNTIME_DIR / "approle" / f"{APPROLE_NAME}.role_id"
SECRET_ID_FILE = RUNTIME_DIR / "approle" / f"{APPROLE_NAME}.secret_id"
RESULT_FILE = RUNTIME_DIR / "generated" / "perf-repl-spiffe-issuer-result.json"
JWT_FILE = RUNTIME_DIR / "generated" / "perf-repl-spiffe-issuer.jwt"


def fetch_oidc_configuration(base_url: str, mount_path: str) -> dict:
    response = requests.get(
        f"{base_url}/v1/{mount_path}/.well-known/openid-configuration",
        verify=str(CA_CERT),
        timeout=20,
    )
    if response.status_code >= 400:
        raise VaultDemoError(
            f"GET {base_url}/v1/{mount_path}/.well-known/openid-configuration failed: "
            f"{response.status_code} {response.text}"
        )
    data = response.json()
    jwks_uri = data.get("jwks_uri")
    if isinstance(jwks_uri, str) and jwks_uri.startswith("/"):
        data["jwks_uri"] = urljoin(f"{base_url}/", jwks_uri.lstrip("/"))
    return data


def collect_once() -> tuple[dict, str]:
    role_id = read_text(ROLE_ID_FILE)
    secret_id = read_text(SECRET_ID_FILE)

    primary_auth = approle_login(PRIMARY_ADDR, str(CA_CERT), role_id, secret_id)
    replica_auth = approle_login(REPLICA_ADDR, str(CA_CERT), role_id, secret_id)

    primary_config = read_vault_path(
        PRIMARY_ADDR,
        str(CA_CERT),
        primary_auth["client_token"],
        f"{TEST_MOUNT}/config",
    )["data"]
    replica_config = read_vault_path(
        REPLICA_ADDR,
        str(CA_CERT),
        replica_auth["client_token"],
        f"{TEST_MOUNT}/config",
    )["data"]

    primary_oidc = fetch_oidc_configuration(PRIMARY_ADDR, TEST_MOUNT)
    replica_oidc = fetch_oidc_configuration(REPLICA_ADDR, TEST_MOUNT)

    jwt_token, mint_data = mint_spiffe_jwt(
        REPLICA_ADDR,
        str(CA_CERT),
        replica_auth["client_token"],
        SPIFFE_ROLE,
        AUDIENCE,
        mount_path=TEST_MOUNT,
    )
    claims = decode_unverified_jwt(jwt_token)

    primary_expected_issuer = f"{PRIMARY_CLUSTER_ADDR}/v1/{TEST_MOUNT}"
    replica_expected_issuer = f"{REPLICA_CLUSTER_ADDR}/v1/{TEST_MOUNT}"
    observed_issuer = claims.get("iss")

    result = {
        "mount_path": TEST_MOUNT,
        "minted_from_cluster": REPLICA_ADDR,
        "primary_expected_issuer": primary_expected_issuer,
        "replica_expected_issuer": replica_expected_issuer,
        "observed_issuer": observed_issuer,
        "observed_issuer_matches_primary_cluster": observed_issuer == primary_expected_issuer,
        "observed_issuer_matches_replica_cluster": observed_issuer == replica_expected_issuer,
        "primary_mount_config": primary_config,
        "replica_mount_config": replica_config,
        "primary_oidc_configuration": primary_oidc,
        "replica_oidc_configuration": replica_oidc,
        "replica_mint_response": mint_data,
        "jwt_claims": claims,
    }
    return result, jwt_token


def main() -> int:
    last_error: Exception | None = None

    for _ in range(30):
        try:
            result, jwt_token = collect_once()
            write_text(RESULT_FILE, json.dumps(result, indent=2) + "\n")
            write_text(JWT_FILE, f"{jwt_token}\n")
            print(json.dumps(result, indent=2))
            return 0
        except (FileNotFoundError, JSONDecodeError, KeyError, RequestException, VaultDemoError) as exc:
            last_error = exc
            time.sleep(2)

    raise RuntimeError(f"Replica issuer validation did not become ready in time: {last_error}")


if __name__ == "__main__":
    raise SystemExit(main())
