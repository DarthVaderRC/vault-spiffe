from __future__ import annotations

import json
import os
import shlex
import subprocess
import textwrap
from pathlib import Path
from typing import Any

from hashibank_demo.checkpoints import ordered_steps

DEMO_COMMAND_CWD = Path("/workspace/demo")
DEFAULT_VAULT_ADDR = "https://hashibank-vault:8200"
DEFAULT_VAULT_CACERT = "config/tls/hashibank-root-ca.crt"


class DemoCommandError(RuntimeError):
    """Raised when a presenter-facing shell command fails."""


_PAUSE_FIRST_CALL_SEEN = False


def _maybe_pause() -> None:
    """Pause before a presenter-facing call so output does not scroll away.

    Gated by HASHIBANK_DEMO_PAUSE (set only by the interactive demo run flows):
    - unset  -> no pausing
    - "first" -> enabled, but skip the pause before this process's first call
                 (used for the very first checkpoint, so the demo does not pause
                 before it has shown anything)
    - "on"    -> enabled, pause before every call including the first (the first
                 call's pause is the inter-checkpoint boundary)

    Pausing *before* every call except the very first == a checkpoint *after*
    every call, with no dangling pause at the end of the demo.
    """
    global _PAUSE_FIRST_CALL_SEEN

    mode = os.environ.get("HASHIBANK_DEMO_PAUSE")
    first_call = not _PAUSE_FIRST_CALL_SEEN
    _PAUSE_FIRST_CALL_SEEN = True

    if not mode:
        return
    if first_call and mode == "first":
        return

    try:
        input("Press Enter to continue...")
    except EOFError:
        return
    print()


def shell_quote(value: str | Path) -> str:
    return shlex.quote(str(value))


def demo_relative_path(value: str | Path) -> str:
    candidate = Path(value)
    try:
        return str(candidate.relative_to(DEMO_COMMAND_CWD))
    except ValueError:
        return str(candidate)


def run_text_command(
    title: str,
    command: str,
    *,
    env: dict[str, str] | None = None,
    show_command: bool = True,
) -> str:
    prepared = _prepare_command(command)
    _maybe_pause()
    _print_heading(title)
    if show_command:
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
    show_command: bool = True,
) -> str:
    vault_env = {
        "VAULT_ADDR": DEFAULT_VAULT_ADDR,
        "VAULT_CACERT": DEFAULT_VAULT_CACERT,
    }
    if token:
        vault_env["VAULT_TOKEN"] = token
    if env:
        vault_env.update({key: str(value) for key, value in env.items()})
    return run_text_command(title, command, env=vault_env, show_command=show_command)


def run_captured(command: str, *, env: dict[str, str] | None = None) -> str:
    """Run a command and return its output without printing anything.

    Used to execute the real (noisy) command behind a step while the presenter
    sees a clean representative command and pretty-printed output instead.
    """
    return _run_shell(_prepare_command(command), env=env)


def print_json(title: str, data: Any, *, command: str | None = None) -> None:
    """Print a heading, an optional clean command line, and pretty JSON.

    The full object is shown (json.dumps indent=2); this is not a curated
    summary.
    """
    _maybe_pause()
    _print_heading(title)
    if command:
        _print_command(_prepare_command(command))
    print(json.dumps(data, indent=2, default=str))


def print_highlights(*lines: str) -> None:
    _ = lines


def print_info(*lines: str) -> None:
    entries = [line for line in lines if line]
    if not entries:
        return
    print()
    for line in entries:
        print(line)


def print_status(state: dict[str, Any], _script_name: str, *, extra_lines: list[str] | None = None) -> None:
    print(f"Scenario: {state['scenario']} ({state['persona']})")
    print()
    for step in ordered_steps(state):
        status = step.get("status", "pending")
        label = step.get("label", step["id"])
        print(f"- {step['id']}: {label} [{status}]")
    for line in extra_lines or []:
        print()
        print(line)


def print_reset(scenario: str, _checkpoint_file: str, *, extra_lines: list[str] | None = None) -> None:
    print(f"Reset {scenario} checkpoint state.")
    for line in extra_lines or []:
        print()
        print(line)


def print_step_footer(state: dict[str, Any], _script_name: str, *, extra_lines: list[str] | None = None) -> None:
    entries = [line for line in extra_lines or [] if line]
    if not entries:
        return
    print()
    for line in entries:
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
