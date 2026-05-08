from __future__ import annotations

from flask import Flask, jsonify, render_template_string

from hashibank_demo.checkpoints import load_saved_state, next_pending_step, ordered_steps

app = Flask(__name__)

SCENARIO = "assistant"
SCRIPT_NAME = "demo-agentic-oidc.sh"
DEFAULT_NEXT_STEP = "approle-login"

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

WAITING_TEMPLATE = """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>HashiBank Relationship Assistant</title>
    <style>
      body { font-family: Arial, sans-serif; background: #111827; color: #f3f4f6; margin: 0; padding: 2rem; }
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
      <p><strong>Next command:</strong> <code>{{ waiting["next_command"] }}</code></p>
      <p class="meta">Checkpoint file: {{ waiting["checkpoint_file"] }}</p>
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
    next_step = next_pending_step(state) or DEFAULT_NEXT_STEP
    return {
        "completed_steps": completed,
        "next_command": f"./scripts/{SCRIPT_NAME} {next_step}",
        "checkpoint_file": "runtime/checkpoints/assistant.json",
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
