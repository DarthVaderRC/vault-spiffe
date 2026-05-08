from __future__ import annotations

import os
import shlex
import subprocess
import textwrap
from pathlib import Path
from typing import Any

from hashibank_demo.checkpoints import (
    display_path,
    next_pending_step,
    ordered_steps,
    scenario_state_path,
)

DEMO_COMMAND_CWD = Path("/workspace/demo")
DEFAULT_VAULT_ADDR = "https://hashibank-vault:8200"
DEFAULT_VAULT_CACERT = "config/tls/hashibank-root-ca.crt"


class DemoCommandError(RuntimeError):
    """Raised when a presenter-facing shell command fails."""


def shell_quote(value: str | Path) -> str:
    return shlex.quote(str(value))


def demo_relative_path(value: str | Path) -> str:
    candidate = Path(value)
    try:
        return str(candidate.relative_to(DEMO_COMMAND_CWD))
    except ValueError:
        return str(candidate)


def run_text_command(title: str, command: str, *, env: dict[str, str] | None = None) -> str:
    prepared = _prepare_command(command)
    _print_heading(title)
    _print_command(prepared)
    output = _run_shell(prepared, env=env)
    _print_output(output)
    return output


def run_vault_command(
    title: str,
    command: str,
    *,
    token: str | None = None,
    env: dict[str, str] | None = None,
) -> str:
    vault_env = {
        "VAULT_ADDR": DEFAULT_VAULT_ADDR,
        "VAULT_CACERT": DEFAULT_VAULT_CACERT,
    }
    if token:
        vault_env["VAULT_TOKEN"] = token
    if env:
        vault_env.update({key: str(value) for key, value in env.items()})
    return run_text_command(title, command, env=vault_env)


def print_highlights(*lines: str) -> None:
    entries = [line for line in lines if line]
    if not entries:
        return
    print()
    print("Highlights:")
    for line in entries:
        print(f"- {line}")


def print_info(*lines: str) -> None:
    entries = [line for line in lines if line]
    if not entries:
        return
    print()
    for line in entries:
        print(line)


def print_status(state: dict[str, Any], script_name: str, *, extra_lines: list[str] | None = None) -> None:
    print(f"Scenario: {state['scenario']} ({state['persona']})")
    print(f"Checkpoint file: {display_path(scenario_state_path(state['scenario']))}")
    print()
    for step in ordered_steps(state):
        status = step.get("status", "pending")
        label = step.get("label", step["id"])
        print(f"- {step['id']}: {label} [{status}]")
    next_step = next_pending_step(state)
    print()
    if next_step:
        print(f"Next command: ./scripts/{script_name} {next_step}")
    else:
        print("Next command: none")
    for line in extra_lines or []:
        print(line)


def print_reset(scenario: str, checkpoint_file: str, *, extra_lines: list[str] | None = None) -> None:
    print(f"Reset {scenario} checkpoint state.")
    print(f"Checkpoint file: {checkpoint_file}")
    for line in extra_lines or []:
        print(line)


def print_step_footer(state: dict[str, Any], script_name: str, *, extra_lines: list[str] | None = None) -> None:
    print()
    print(f"Checkpoint file: {display_path(scenario_state_path(state['scenario']))}")
    next_step = next_pending_step(state)
    if next_step:
        print(f"Next command: ./scripts/{script_name} {next_step}")
    else:
        print("Scenario complete.")
    for line in extra_lines or []:
        print(line)


def _prepare_command(command: str) -> str:
    return textwrap.dedent(command).strip()


def _print_heading(title: str) -> None:
    print()
    print(f"=== {title} ===")
    print()


def _print_command(command: str) -> None:
    lines = command.splitlines()
    if not lines:
        return
    print(f"$ {lines[0]}")
    for line in lines[1:]:
        print(f"  {line}")
    print()


def _print_output(output: str) -> None:
    if output:
        print(output)
    else:
        print("(no output)")


def _run_shell(command: str, *, env: dict[str, str] | None = None) -> str:
    command_env = os.environ.copy()
    if env:
        command_env.update({key: str(value) for key, value in env.items()})
    completed = subprocess.run(
        ["bash", "-lc", f"set -euo pipefail\n{command}"],
        capture_output=True,
        text=True,
        cwd=DEMO_COMMAND_CWD,
        env=command_env,
    )
    if completed.returncode != 0:
        error_output = completed.stderr.strip() or completed.stdout.strip() or f"command failed: {command}"
        raise DemoCommandError(error_output)
    return completed.stdout.rstrip()
