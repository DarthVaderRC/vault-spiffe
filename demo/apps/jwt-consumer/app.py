from __future__ import annotations

import os
import time
from http import HTTPStatus
from urllib.parse import urljoin, urlparse, urlunparse

import jwt
import requests
from flask import Flask, jsonify, request


PORT = int(os.environ.get("PORT", "8080"))
VAULT_DISCOVERY_URL = os.environ["VAULT_DISCOVERY_URL"]
ROOT_CA_FILE = os.environ["ROOT_CA_FILE"]
EXPECTED_AUDIENCE = os.environ["EXPECTED_AUDIENCE"]
ALLOWED_SPIFFE_SUBJECT = os.environ["ALLOWED_SPIFFE_SUBJECT"]
REQUIRED_APPLICATION = os.environ["REQUIRED_APPLICATION"]
REQUIRED_LINE_OF_BUSINESS = os.environ["REQUIRED_LINE_OF_BUSINESS"]
REQUIRED_CUSTOMER_DATA_DOMAIN = os.environ["REQUIRED_CUSTOMER_DATA_DOMAIN"]
BUSINESS_CLAIMS = (
    "bank",
    "application",
    "line_of_business",
    "environment",
    "customer_data_domain",
    "kubernetes_service_account",
)
JWKS_CACHE_TTL_SECONDS = 300
RELATIONSHIP_INSIGHTS = [
    {
        "customer": "Avery Family Office",
        "segment": "Private banking",
        "masked_accounts": ["**** 1042", "**** 7781"],
        "context": "Idle balances increased after a municipal bond maturity and the client has not selected a reinvestment plan.",
        "relationship_manager": "Camila Ross",
        "priority": "HIGH",
    },
    {
        "customer": "Northwind Manufacturing",
        "segment": "Commercial",
        "masked_accounts": ["**** 5510", "**** 2219"],
        "context": "Treasury sweeps are disabled on the main operating account and payroll outflows now exceed the target buffer.",
        "relationship_manager": "Ethan Price",
        "priority": "MEDIUM",
    },
    {
        "customer": "Lattice Health Partners",
        "segment": "Mid-market healthcare",
        "masked_accounts": ["**** 8893"],
        "context": "Foreign-currency receivables increased and the client asked for a same-day cash-position summary before market close.",
        "relationship_manager": "Noor Haddad",
        "priority": "HIGH",
    },
]
NEXT_BEST_ACTION = {
    "title": "Schedule a same-day liquidity review with Avery Family Office",
    "reason": "The relationship-assistant detected excess idle cash and an open reinvestment decision window.",
    "priority": "HIGH",
    "service_level": "within 4 hours",
}

app = Flask(__name__)
_discovery_cache: dict | None = None
_jwks_cache: dict | None = None
_jwks_cache_expires_at = 0.0


def _load_discovery(*, force_refresh: bool = False) -> dict:
    global _discovery_cache
    if _discovery_cache is not None and not force_refresh:
        return _discovery_cache

    response = requests.get(VAULT_DISCOVERY_URL, timeout=10, verify=ROOT_CA_FILE)
    response.raise_for_status()
    discovery = response.json()
    jwks_uri = discovery.get("jwks_uri")
    discovery_url = urlparse(VAULT_DISCOVERY_URL)
    if isinstance(jwks_uri, str) and jwks_uri.startswith("/"):
        discovery["jwks_uri"] = urljoin(VAULT_DISCOVERY_URL, jwks_uri)
    elif isinstance(jwks_uri, str):
        parsed_jwks = urlparse(jwks_uri)
        if parsed_jwks.netloc and parsed_jwks.netloc != discovery_url.netloc:
            discovery["jwks_uri"] = urlunparse(
                (
                    discovery_url.scheme,
                    discovery_url.netloc,
                    parsed_jwks.path,
                    parsed_jwks.params,
                    parsed_jwks.query,
                    parsed_jwks.fragment,
                )
            )
    _discovery_cache = discovery
    return discovery


def _load_jwks(jwks_uri: str, *, force_refresh: bool = False) -> dict:
    global _jwks_cache, _jwks_cache_expires_at
    now = time.monotonic()
    if _jwks_cache is not None and not force_refresh and now < _jwks_cache_expires_at:
        return _jwks_cache

    response = requests.get(jwks_uri, timeout=10, verify=ROOT_CA_FILE)
    response.raise_for_status()
    _jwks_cache = response.json()
    _jwks_cache_expires_at = now + JWKS_CACHE_TTL_SECONDS
    return _jwks_cache


def _claims_authorized(claims: dict) -> tuple[bool, str]:
    if claims["sub"] != ALLOWED_SPIFFE_SUBJECT:
        return False, "subject not authorized"
    if claims.get("application") != REQUIRED_APPLICATION:
        return False, "application claim not authorized"
    if claims.get("line_of_business") != REQUIRED_LINE_OF_BUSINESS:
        return False, "line_of_business claim not authorized"
    if claims.get("customer_data_domain") != REQUIRED_CUSTOMER_DATA_DOMAIN:
        return False, "customer_data_domain claim not authorized"
    return True, ""


def _validate_token(token: str) -> dict:
    discovery = _load_discovery()
    header = jwt.get_unverified_header(token)
    for force_refresh in (False, True):
        jwks = _load_jwks(discovery["jwks_uri"], force_refresh=force_refresh)
        for entry in jwks.get("keys", []):
            if entry.get("kid") == header.get("kid"):
                signing_key = jwt.PyJWK.from_dict(entry).key
                return jwt.decode(
                    token,
                    signing_key,
                    algorithms=[header.get("alg", "RS256")],
                    audience=EXPECTED_AUDIENCE,
                    issuer=discovery["issuer"],
                )
    raise ValueError(f"Unable to find signing key for kid={header.get('kid')}")


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True})


@app.get("/api/relationship-insights")
def relationship_insights():
    auth_header = request.headers.get("Authorization", "")
    prefix = "Bearer "
    if not auth_header.startswith(prefix):
        return jsonify({"error": "missing bearer token"}), HTTPStatus.UNAUTHORIZED

    try:
        claims = _validate_token(auth_header[len(prefix) :])
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), HTTPStatus.UNAUTHORIZED

    authorized, reason = _claims_authorized(claims)
    if not authorized:
        return jsonify({"error": reason}), HTTPStatus.FORBIDDEN

    validated_claims = {
        "sub": claims["sub"],
        "iss": claims["iss"],
        "aud": claims["aud"],
    }
    for claim in BUSINESS_CLAIMS:
        if claim in claims:
            validated_claims[claim] = claims[claim]

    return jsonify(
        {
            "message": "JWT-SVID authorized for cross-network relationship insights",
            "validated_claims": validated_claims,
            "insights": RELATIONSHIP_INSIGHTS,
            "next_best_action": NEXT_BEST_ACTION,
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
