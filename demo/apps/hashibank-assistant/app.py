from __future__ import annotations

import os

from flask import Flask, jsonify, render_template_string

from hashibank_demo.vault_client import (
    approle_login,
    fetch_oidc_configuration,
    mint_spiffe_jwt,
    read_text,
    validate_spiffe_jwt,
)

app = Flask(__name__)

MASKED_CONTEXT = [
    {
        "relationship_manager": "S. Chen",
        "customer": "A**** M******",
        "segment": "Premier Retail",
        "context": "Mortgage refinance inquiry with high savings balance",
    },
    {
        "relationship_manager": "T. Singh",
        "customer": "R*** L**",
        "segment": "Business Banking",
        "context": "Treasury onboarding follow-up with pending KYC document review",
    },
    {
        "relationship_manager": "M. Alvarez",
        "customer": "J*** P******",
        "segment": "Private Banking",
        "context": "Portfolio review request after large outbound transfer",
    },
]

PAGE_TEMPLATE = """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>HashiBank Relationship Assistant</title>
    <style>
      body { font-family: Arial, sans-serif; background: #111827; color: #f3f4f6; margin: 0; padding: 2rem; }
      h1 { margin-top: 0; }
      .card { background: #1f2937; border-radius: 12px; padding: 1rem 1.25rem; margin-bottom: 1rem; }
      .pill { display: inline-block; background: #0f766e; color: #ecfeff; border-radius: 999px; padding: 0.2rem 0.6rem; font-size: 0.85rem; }
      code { color: #fde68a; }
      ul { padding-left: 1rem; }
      li { margin-bottom: 0.75rem; }
    </style>
  </head>
  <body>
    <h1>HashiBank Relationship Assistant</h1>
    <div class="card">
      <div class="pill">OIDC-validated SPIFFE workload</div>
      <p><strong>SPIFFE subject:</strong> <code>{{ payload["validated_claims"]["sub"] }}</code></p>
      <p><strong>Issuer:</strong> <code>{{ payload["validated_claims"]["iss"] }}</code></p>
      <p><strong>Audience:</strong> <code>{{ payload["validated_claims"]["aud"] }}</code></p>
    </div>
    <div class="card">
      <h2>Masked banker context</h2>
      <ul>
        {% for row in payload["contexts"] %}
        <li>
          <strong>{{ row["customer"] }}</strong> — {{ row["segment"] }}<br>
          {{ row["context"] }}<br>
          <em>Relationship manager: {{ row["relationship_manager"] }}</em>
        </li>
        {% endfor %}
      </ul>
    </div>
  </body>
</html>
"""

ERROR_TEMPLATE = """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>HashiBank Relationship Assistant</title>
  </head>
  <body>
    <h1>Relationship assistant demo error</h1>
    <pre>{{ error }}</pre>
  </body>
</html>
"""


def build_demo_payload() -> dict:
    # This flow validates the SPIFFE JWT outside Vault auth to show that a relying
    # party can trust Vault-published OIDC metadata and JWKS directly.
    issuer_auth = approle_login(
        os.environ["HASHIBANK_IDENTITY_ADDR"],
        os.environ["HASHIBANK_CA_CERT"],
        read_text(os.environ["APPROLE_ROLE_ID_FILE"]),
        read_text(os.environ["APPROLE_SECRET_ID_FILE"]),
    )
    jwt_token, _ = mint_spiffe_jwt(
        os.environ["HASHIBANK_IDENTITY_ADDR"],
        os.environ["HASHIBANK_CA_CERT"],
        issuer_auth["client_token"],
        os.environ["SPIFFE_ROLE"],
        os.environ["SPIFFE_AUDIENCE"],
    )
    # The relying-party code resolves discovery and keys from the SPIFFE issuer
    # instead of depending on a Vault-native auth mount.
    discovery = fetch_oidc_configuration(
        os.environ["HASHIBANK_IDENTITY_ADDR"],
        os.environ["HASHIBANK_CA_CERT"],
    )
    validated_claims = validate_spiffe_jwt(
        jwt_token,
        issuer=discovery["issuer"],
        audience=os.environ["SPIFFE_AUDIENCE"],
        jwks_uri=discovery["jwks_uri"],
        ca_cert=os.environ["HASHIBANK_CA_CERT"],
    )
    return {
        "persona": "relationship-assistant",
        "validated_claims": {
            "sub": validated_claims["sub"],
            "iss": validated_claims["iss"],
            "aud": validated_claims["aud"],
            "vault_entity_id": validated_claims.get("vault", {}).get("entity", {}).get("id"),
        },
        "contexts": MASKED_CONTEXT,
    }


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True})


@app.get("/api/demo")
def api_demo():
    try:
        return jsonify(build_demo_payload())
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


@app.get("/")
def index():
    try:
        return render_template_string(PAGE_TEMPLATE, payload=build_demo_payload())
    except Exception as exc:  # noqa: BLE001
        return render_template_string(ERROR_TEMPLATE, error=str(exc)), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
