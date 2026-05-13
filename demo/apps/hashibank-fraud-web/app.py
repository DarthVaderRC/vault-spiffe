from __future__ import annotations

from flask import Flask, jsonify, render_template_string

from hashibank_demo.checkpoints import load_saved_state, ordered_steps

app = Flask(__name__)

SCENARIO = "fraud"
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
      <div class="meta">Prepared from CLI checkpoint state</div>
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

WAITING_TEMPLATE = """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>HashiBank Fraud Ops</title>
    <style>
      body { font-family: Arial, sans-serif; background: #0f172a; color: #e2e8f0; margin: 0; padding: 2rem; }
      h1 { margin-top: 0; }
      .card { background: #111827; border: 1px solid #1f2937; border-radius: 12px; padding: 1rem 1.25rem; margin-bottom: 1rem; }
      code { color: #86efac; }
      li { margin-bottom: 0.4rem; }
      .meta { color: #94a3b8; }
    </style>
  </head>
    <body>
      <h1>HashiBank Fraud Ops</h1>
      <div class="card">
        <p>This page waits for prepared demo state. It does not rerun Vault login or database access on page load.</p>
      </div>
      <div class="card">
        <h2>Completed checkpoints</h2>
      <ul>
        {% if waiting["completed_steps"] %}
          {% for step in waiting["completed_steps"] %}
          <li>{{ step }}</li>
          {% endfor %}
        {% else %}
          <li>None yet</li>
        {% endif %}
      </ul>
    </div>
  </body>
</html>
"""


def build_waiting_state() -> dict:
    state = load_saved_state(SCENARIO) or {}
    completed = [step["label"] for step in ordered_steps(state) if step.get("status") == "completed"]
    return {
        "completed_steps": completed,
    }


def prepared_payload() -> dict | None:
    state = load_saved_state(SCENARIO)
    if not state or not state.get("ready"):
        return None
    return state.get("prepared_payload")


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True})


@app.get("/api/demo")
def api_demo():
    payload = prepared_payload()
    if payload is None:
        waiting = build_waiting_state()
        return jsonify({"error": "final reveal not prepared", **waiting}), 409
    return jsonify(payload)


@app.get("/")
def index():
    payload = prepared_payload()
    if payload is None:
        return render_template_string(WAITING_TEMPLATE, waiting=build_waiting_state())
    return render_template_string(PAGE_TEMPLATE, payload=payload)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
