from __future__ import annotations

import os

from flask import Flask, jsonify, render_template_string

from hashibank_demo.checkpoints import load_saved_state, ordered_steps

app = Flask(__name__)

SCENARIO = os.environ.get("HASHIBANK_DEMO_SCENARIO", "k8s-jwt")
PAGE_TEMPLATE = """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>HashiBank Relationship Assistant</title>
    <style>
      @import url("https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap");
      :root {
        --bg: #0f1720;
        --panel: #16202b;
        --panel-border: #29435c;
        --text: #f4f7fb;
        --muted: #b6c2d0;
        --accent: #0f62fe;
        --accent-soft: rgba(15, 98, 254, 0.16);
        --success: #24a148;
      }
      * { box-sizing: border-box; }
      body {
        font-family: "IBM Plex Sans", sans-serif;
        background:
          radial-gradient(circle at top right, rgba(15, 98, 254, 0.22), transparent 28%),
          linear-gradient(180deg, #0b1219 0%, var(--bg) 100%);
        color: var(--text);
        margin: 0;
        padding: 2rem;
      }
      h1, h2, h3, p { margin-top: 0; }
      .shell { max-width: 1080px; margin: 0 auto; }
      .eyebrow {
        font-family: "IBM Plex Mono", monospace;
        text-transform: uppercase;
        letter-spacing: 0.18em;
        font-size: 0.76rem;
        color: var(--muted);
        margin-bottom: 0.75rem;
      }
      .lede { color: var(--muted); max-width: 58rem; }
      .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1rem; margin: 1.5rem 0; }
      .card {
        background: linear-gradient(180deg, rgba(22, 32, 43, 0.94), rgba(15, 23, 32, 0.94));
        border: 1px solid var(--panel-border);
        border-radius: 18px;
        padding: 1.25rem 1.4rem;
        box-shadow: 0 18px 48px rgba(0, 0, 0, 0.22);
      }
      .pill {
        display: inline-block;
        background: var(--accent-soft);
        color: #d9e9ff;
        border: 1px solid rgba(15, 98, 254, 0.45);
        border-radius: 999px;
        padding: 0.24rem 0.7rem;
        font-size: 0.82rem;
        margin-bottom: 0.8rem;
      }
      .claim {
        margin-bottom: 0.7rem;
        color: var(--muted);
      }
      .claim strong {
        color: var(--text);
        display: block;
        margin-bottom: 0.2rem;
      }
      code {
        font-family: "IBM Plex Mono", monospace;
        color: #a9d7ff;
        word-break: break-word;
      }
      ul { padding-left: 1rem; margin: 0; }
      li { margin-bottom: 0.9rem; }
      .insight-title { font-weight: 600; margin-bottom: 0.25rem; }
      .meta { color: var(--muted); font-size: 0.94rem; }
      .action {
        border-left: 3px solid var(--success);
        background: rgba(36, 161, 72, 0.12);
      }
    </style>
  </head>
  <body>
    <div class="shell">
      <div class="eyebrow">Cross-network API authorization</div>
      <h1>HashiBank Relationship Assistant</h1>
      <p class="lede">The relationship-assistant workload presents a Vault-minted JWT-SVID to the downstream relationship insights API. The API validates the signature through discovery and JWKS, authorizes the workload and business claims, then returns masked banker context plus the next-best action.</p>

      <div class="grid">
        <div class="card">
          <div class="pill">JWT-SVID accepted</div>
          <div class="claim"><strong>SPIFFE subject</strong><code>{{ payload["validated_claims"]["sub"] }}</code></div>
          <div class="claim"><strong>Issuer</strong><code>{{ payload["validated_claims"]["iss"] }}</code></div>
          <div class="claim"><strong>Audience</strong><code>{{ payload["validated_claims"]["aud"] }}</code></div>
          <div class="claim"><strong>Line of business</strong>{{ payload["validated_claims"]["line_of_business"] }}</div>
          <div class="claim"><strong>Customer data domain</strong>{{ payload["validated_claims"]["customer_data_domain"] }}</div>
        </div>

        <div class="card action">
          <div class="pill">Next-best action</div>
          <h2>{{ payload["next_best_action"]["title"] }}</h2>
          <p>{{ payload["next_best_action"]["reason"] }}</p>
          <p class="meta">Priority: {{ payload["next_best_action"]["priority"] }} · Service level: {{ payload["next_best_action"]["service_level"] }}</p>
        </div>
      </div>

      <div class="card">
        <h2>Masked relationship insights</h2>
        <ul>
          {% for row in payload["insights"] %}
          <li>
            <div class="insight-title">{{ row["customer"] }} — {{ row["segment"] }}</div>
            <div>{{ row["context"] }}</div>
            <div class="meta">Accounts: {{ row["masked_accounts"]|join(", ") }} · Relationship manager: {{ row["relationship_manager"] }} · Priority: {{ row["priority"] }}</div>
          </li>
          {% endfor %}
        </ul>
      </div>
    </div>
  </body>
</html>
"""

WAITING_TEMPLATE = """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>HashiBank Relationship Assistant</title>
    <style>
      @import url("https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap");
      body { font-family: "IBM Plex Sans", sans-serif; background: #111827; color: #f3f4f6; margin: 0; padding: 2rem; }
      h1 { margin-top: 0; }
      .card { background: #1f2937; border-radius: 12px; padding: 1rem 1.25rem; margin-bottom: 1rem; }
      code { color: #fde68a; }
      li { margin-bottom: 0.4rem; }
      .meta { color: #d1d5db; }
    </style>
  </head>
    <body>
      <h1>HashiBank Relationship Assistant</h1>
      <div class="card">
        <p>This page waits for prepared demo state. It does not mint or validate a SPIFFE JWT on page load.</p>
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
