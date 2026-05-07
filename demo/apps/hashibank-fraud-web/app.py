from __future__ import annotations

import os

from flask import Flask, jsonify, render_template_string
from psycopg import connect
from psycopg.rows import dict_row

from hashibank_demo.vault_client import (
    approle_login,
    decode_unverified_jwt,
    mint_spiffe_jwt,
    read_text,
    read_vault_path,
    spiffe_login_jwt,
)

app = Flask(__name__)

PAGE_TEMPLATE = """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>HashiBank Fraud Ops</title>
    <style>
      body { font-family: Arial, sans-serif; background: #0f172a; color: #e2e8f0; margin: 0; padding: 2rem; }
      h1 { margin-top: 0; }
      .card { background: #111827; border: 1px solid #1f2937; border-radius: 12px; padding: 1rem 1.25rem; margin-bottom: 1rem; }
      table { width: 100%; border-collapse: collapse; margin-top: 1rem; }
      th, td { border-bottom: 1px solid #334155; padding: 0.75rem; text-align: left; }
      th { color: #93c5fd; }
      .meta { color: #94a3b8; font-size: 0.95rem; }
      code { color: #86efac; }
    </style>
  </head>
  <body>
    <h1>HashiBank Fraud Ops</h1>
    <div class="card">
      <div class="meta">SPIFFE subject</div>
      <code>{{ payload["spiffe_subject"] }}</code>
      <p class="meta">Vault policies: {{ payload["vault_policies"]|join(", ") }}</p>
      <p class="meta">Dynamic DB username: {{ payload["db_username"] }}</p>
      <p class="meta">Lease: {{ payload["db_lease_id"] }} ({{ payload["db_lease_duration"] }}s)</p>
    </div>
    <div class="card">
      <h2>Flagged transactions</h2>
      <table>
        <thead>
          <tr>
            <th>Account</th>
            <th>Severity</th>
            <th>Status</th>
            <th>Amount</th>
            <th>Merchant</th>
            <th>Event time</th>
          </tr>
        </thead>
        <tbody>
          {% for row in payload["rows"] %}
          <tr>
            <td>{{ row["account_mask"] }}</td>
            <td>{{ row["severity"] }}</td>
            <td>{{ row["status"] }}</td>
            <td>${{ "%.2f"|format(row["amount"]) }}</td>
            <td>{{ row["merchant"] }}</td>
            <td>{{ row["event_time"] }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </body>
</html>
"""

ERROR_TEMPLATE = """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>HashiBank Fraud Ops</title>
  </head>
  <body>
    <h1>Fraud Ops demo error</h1>
    <pre>{{ error }}</pre>
  </body>
</html>
"""


def query_fraud_alerts(username: str, password: str) -> list[dict]:
    with connect(
        host=os.environ["POSTGRES_HOST"],
        port=int(os.environ["POSTGRES_PORT"]),
        dbname=os.environ["POSTGRES_DB"],
        user=username,
        password=password,
        sslmode="disable",
        row_factory=dict_row,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT account_mask, severity, status, amount, merchant, event_time
                FROM fraud_alerts
                ORDER BY event_time DESC
                LIMIT 5
                """
            )
            return [dict(row) for row in cur.fetchall()]


def build_demo_payload() -> dict:
    # This flow deliberately chains workload identity to a business outcome:
    # AppRole -> JWT-SVID -> SPIFFE auth -> dynamic DB credentials -> SQL query.
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
    # Decode without verification only so the UI can show the SPIFFE subject; the
    # actual access decision comes from the verified SPIFFE login below.
    jwt_claims = decode_unverified_jwt(jwt_token)
    access_auth = spiffe_login_jwt(
        os.environ["HASHIBANK_ACCESS_ADDR"],
        os.environ["HASHIBANK_CA_CERT"],
        mount_path=os.environ["SPIFFE_AUTH_PATH"],
        role=os.environ["SPIFFE_ROLE"],
        svid=jwt_token,
    )
    # Use the relying-party Vault token, not the issuer token, to fetch the short-lived
    # database credential that backs the fraud dashboard query.
    db_secret = read_vault_path(
        os.environ["HASHIBANK_ACCESS_ADDR"],
        os.environ["HASHIBANK_CA_CERT"],
        access_auth["client_token"],
        os.environ["DB_CREDS_PATH"],
    )
    rows = query_fraud_alerts(
        db_secret["data"]["username"],
        db_secret["data"]["password"],
    )

    return {
        "persona": "fraud-ops-web",
        "spiffe_subject": jwt_claims["sub"],
        "vault_entity_id": jwt_claims.get("vault", {}).get("entity", {}).get("id"),
        "vault_policies": access_auth.get("policies", []),
        "db_username": db_secret["data"]["username"],
        "db_lease_id": db_secret.get("lease_id"),
        "db_lease_duration": db_secret.get("lease_duration"),
        "rows": rows,
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
