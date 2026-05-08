from __future__ import annotations

import json
import os
from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATE_VERSION = 1


@dataclass(frozen=True)
class DemoStep:
    id: str
    label: str
    phase: str


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def runtime_dir() -> Path:
    explicit = os.environ.get("HASHIBANK_DEMO_RUNTIME")
    candidates = [Path(explicit)] if explicit else []
    candidates.extend((Path("/workspace/runtime"), Path("/workspace/demo/runtime")))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0] if candidates else Path("/workspace/demo/runtime")


def checkpoint_dir() -> Path:
    target = runtime_dir() / "checkpoints"
    target.mkdir(parents=True, exist_ok=True)
    return target


def scenario_state_path(scenario: str) -> Path:
    return checkpoint_dir() / f"{scenario}.json"


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(runtime_dir().parent))
    except ValueError:
        return str(path)


def _default_step_state(step: DemoStep) -> dict[str, Any]:
    return {
        "id": step.id,
        "label": step.label,
        "phase": step.phase,
        "status": "pending",
    }


def empty_state(scenario: str, persona: str, steps: list[DemoStep]) -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "scenario": scenario,
        "persona": persona,
        "ready": False,
        "current_step": None,
        "prepared_payload": None,
        "step_order": [step.id for step in steps],
        "steps": {step.id: _default_step_state(step) for step in steps},
    }


def load_state(scenario: str, persona: str, steps: list[DemoStep]) -> dict[str, Any]:
    state = empty_state(scenario, persona, steps)
    path = scenario_state_path(scenario)
    if not path.exists():
        return state

    loaded = json.loads(path.read_text(encoding="utf-8"))
    state["version"] = loaded.get("version", STATE_VERSION)
    state["ready"] = bool(loaded.get("ready")) and loaded.get("prepared_payload") is not None
    state["current_step"] = loaded.get("current_step")
    state["prepared_payload"] = loaded.get("prepared_payload")
    state["created_at"] = loaded.get("created_at")
    state["updated_at"] = loaded.get("updated_at")

    loaded_steps = loaded.get("steps", {})
    for step in steps:
        merged = _default_step_state(step)
        existing = loaded_steps.get(step.id, {})
        for key in ("status", "completed_at", "summary", "artifacts"):
            if key in existing:
                merged[key] = existing[key]
        state["steps"][step.id] = merged
    return state


def load_saved_state(scenario: str) -> dict[str, Any] | None:
    path = scenario_state_path(scenario)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_state(scenario: str, state: dict[str, Any]) -> None:
    path = scenario_state_path(scenario)
    timestamp = _timestamp()
    state.setdefault("created_at", timestamp)
    state["updated_at"] = timestamp
    content = f"{json.dumps(state, indent=2, default=_json_default)}\n"
    temp_path = path.with_suffix(f"{path.suffix}.tmp")
    temp_path.write_text(content, encoding="utf-8")
    temp_path.replace(path)


def reset_state(scenario: str) -> None:
    scenario_state_path(scenario).unlink(missing_ok=True)


def ordered_steps(state: dict[str, Any]) -> list[dict[str, Any]]:
    steps = state.get("steps", {})
    order = state.get("step_order", list(steps))
    return [steps[step_id] for step_id in order if step_id in steps]


def completed_steps(state: dict[str, Any]) -> list[str]:
    return [step["id"] for step in ordered_steps(state) if step.get("status") == "completed"]


def next_pending_step(state: dict[str, Any]) -> str | None:
    for step in ordered_steps(state):
        if step.get("status") != "completed":
            return step["id"]
    return None


def require_step_dependencies(state: dict[str, Any], steps: list[DemoStep], step_id: str) -> None:
    missing: list[str] = []
    for step in steps:
        if step.id == step_id:
            break
        if state["steps"][step.id].get("status") != "completed":
            missing.append(step.id)
    if missing:
        raise RuntimeError(f"{step_id} requires completed checkpoints: {', '.join(missing)}")


def invalidate_from_step(state: dict[str, Any], steps: list[DemoStep], step_id: str) -> None:
    invalidating = False
    for step in steps:
        if step.id == step_id:
            invalidating = True
        if invalidating:
            state["steps"][step.id] = _default_step_state(step)
    state["ready"] = False
    state["prepared_payload"] = None


def record_step(
    state: dict[str, Any],
    steps: list[DemoStep],
    step_id: str,
    *,
    summary: dict[str, Any],
    artifacts: dict[str, Any] | None = None,
    prepared_payload: dict[str, Any] | None = None,
) -> None:
    invalidate_from_step(state, steps, step_id)
    step_state = state["steps"][step_id]
    step_state["status"] = "completed"
    step_state["completed_at"] = _timestamp()
    step_state["summary"] = summary
    if artifacts:
        step_state["artifacts"] = artifacts
    state["current_step"] = step_id
    state["prepared_payload"] = prepared_payload
    state["ready"] = prepared_payload is not None


def step_artifacts(state: dict[str, Any], step_id: str) -> dict[str, Any]:
    if state["steps"][step_id].get("status") != "completed":
        raise RuntimeError(f"{step_id} has not completed yet")
    return state["steps"][step_id].get("artifacts", {})


def build_command_output(
    state: dict[str, Any],
    script_name: str,
    *,
    command: str,
    summary: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
    include_steps: bool = False,
) -> dict[str, Any]:
    next_step = next_pending_step(state)
    output = {
        "scenario": state["scenario"],
        "persona": state["persona"],
        "command": command,
        "checkpoint_file": display_path(scenario_state_path(state["scenario"])),
        "ready": state.get("ready", False),
        "completed_steps": completed_steps(state),
        "next_command": f"./scripts/{script_name} {next_step}" if next_step else None,
    }
    if summary is not None:
        output["summary"] = summary
    if include_steps:
        output["steps"] = [_public_step_state(step) for step in ordered_steps(state)]
    if extra:
        output.update(extra)
    return output


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _public_step_state(step: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in step.items() if key != "artifacts"}
