from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin

import jwt
import requests
from cryptography import x509
from cryptography.x509.oid import ExtensionOID


class VaultDemoError(RuntimeError):
    """Raised when a Vault demo request fails."""


def read_text(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8").strip()


def write_text(path: str | Path, content: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _request(
    method: str,
    url: str,
    ca_cert: str,
    *,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    cert: tuple[str, str] | None = None,
) -> dict[str, Any]:
    # Keep the demo client intentionally narrow: every request is TLS-verified,
    # JSON-based, and surfaces failures immediately instead of hiding them.
    response = requests.request(
        method,
        url,
        headers=headers,
        json=payload,
        verify=ca_cert,
        cert=cert,
        timeout=20,
    )
    if response.status_code >= 400:
        raise VaultDemoError(f"{method} {url} failed: {response.status_code} {response.text}")
    if not response.text:
        return {}
    return response.json()


def approle_login(base_url: str, ca_cert: str, role_id: str, secret_id: str) -> dict[str, Any]:
    data = _request(
        "POST",
        f"{base_url}/v1/auth/approle/login",
        ca_cert,
        payload={"role_id": role_id, "secret_id": secret_id},
    )
    return data["auth"]


def mint_spiffe_jwt(
    base_url: str,
    ca_cert: str,
    token: str,
    role: str,
    audience: str,
) -> tuple[str, dict[str, Any]]:
    data = _request(
        "POST",
        f"{base_url}/v1/spiffe/role/{quote(role, safe='')}/mintjwt",
        ca_cert,
        headers={"X-Vault-Token": token},
        payload={"audience": audience},
    )
    # Vault's SPIFFE mintjwt endpoint returns the JWT-SVID in the "token" field,
    # not under an "auth" stanza like a Vault login response.
    token_value = data.get("data", {}).get("token")
    if not token_value:
        raise VaultDemoError(f"SPIFFE mintjwt response did not contain a token field: {data}")
    return token_value, data.get("data", {})


def issue_certificate(
    base_url: str,
    ca_cert: str,
    token: str,
    role: str,
    *,
    common_name: str,
    uri_sans: str,
    ttl: str,
) -> dict[str, Any]:
    data = _request(
        "POST",
        f"{base_url}/v1/pki/issue/{quote(role, safe='')}",
        ca_cert,
        headers={"X-Vault-Token": token},
        payload={
            "common_name": common_name,
            "uri_sans": uri_sans,
            "ttl": ttl,
        },
    )
    return data["data"]


def spiffe_login_jwt(
    base_url: str,
    ca_cert: str,
    *,
    mount_path: str,
    role: str,
    svid: str,
) -> dict[str, Any]:
    data = _request(
        "POST",
        f"{base_url}/v1/auth/{mount_path}/login",
        ca_cert,
        headers={"Authorization": f"Bearer {svid}"},
        payload={"role": role, "type": "jwt"},
    )
    return data["auth"]


def spiffe_login_x509(
    base_url: str,
    ca_cert: str,
    *,
    mount_path: str,
    role: str,
    cert_file: str,
    key_file: str,
) -> dict[str, Any]:
    data = _request(
        "POST",
        f"{base_url}/v1/auth/{mount_path}/login",
        ca_cert,
        payload={"role": role, "type": "cert"},
        cert=(cert_file, key_file),
    )
    return data["auth"]


def read_vault_path(base_url: str, ca_cert: str, token: str, path: str) -> dict[str, Any]:
    return _request(
        "GET",
        f"{base_url}/v1/{path}",
        ca_cert,
        headers={"X-Vault-Token": token},
    )


def decode_unverified_jwt(token: str) -> dict[str, Any]:
    return jwt.decode(
        token,
        options={
            "verify_signature": False,
            "verify_exp": False,
            "verify_aud": False,
        },
    )


def jwt_has_expired(token: str, *, leeway_seconds: int = 0) -> bool:
    claims = decode_unverified_jwt(token)
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)):
        return False
    return datetime.now(timezone.utc) >= datetime.fromtimestamp(exp, tz=timezone.utc) - timedelta(seconds=leeway_seconds)


def fetch_oidc_configuration(base_url: str, ca_cert: str) -> dict[str, Any]:
    data = _request(
        "GET",
        f"{base_url}/v1/spiffe/.well-known/openid-configuration",
        ca_cert,
    )
    jwks_uri = data.get("jwks_uri")
    # Normalize relative JWKS URLs so the relying-party code can call the discovery
    # document without knowing whether Vault emitted an absolute or relative path.
    if isinstance(jwks_uri, str) and jwks_uri.startswith("/"):
        data["jwks_uri"] = urljoin(f"{base_url}/", jwks_uri.lstrip("/"))
    return data


def validate_spiffe_jwt(
    token: str,
    *,
    issuer: str,
    audience: str,
    jwks_uri: str,
    ca_cert: str,
) -> dict[str, Any]:
    header = jwt.get_unverified_header(token)
    key_id = header.get("kid")
    jwks = _request("GET", jwks_uri, ca_cert)
    # Match the JWT header kid to the published JWKS entry so validation keeps
    # working even when the SPIFFE issuer rotates signing keys.
    for entry in jwks.get("keys", []):
        if entry.get("kid") == key_id:
            signing_key = jwt.PyJWK.from_dict(entry).key
            return jwt.decode(
                token,
                signing_key,
                algorithms=[header.get("alg", "RS256")],
                audience=audience,
                issuer=issuer,
            )
    raise VaultDemoError(f"Unable to find signing key for kid={key_id}")


def extract_uri_sans(certificate_pem: str) -> list[str]:
    cert = x509.load_pem_x509_certificate(certificate_pem.encode("utf-8"))
    extension = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
    sans = extension.value
    return list(sans.get_values_for_type(x509.UniformResourceIdentifier))


def certificate_has_expired(certificate_pem: str, *, leeway_seconds: int = 0) -> bool:
    cert = x509.load_pem_x509_certificate(certificate_pem.encode("utf-8"))
    if hasattr(cert, "not_valid_after_utc"):
        not_after = cert.not_valid_after_utc
    else:
        not_after = cert.not_valid_after.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= not_after - timedelta(seconds=leeway_seconds)
