"""Execute every discovered command and capture results."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass

from parser import HelpTree, Command, Option


@dataclass
class CommandResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


def run_all_commands(
    tree: HelpTree,
    timeout: int = 10,
    dry_run: bool = False,
) -> list[CommandResult]:
    """Run every command/option combo discovered in the help tree."""
    results: list[CommandResult] = []

    # 1. Top-level --help (already succeeded if we got here)
    results.append(_make_result(f"{tree.binary} --help", 0, tree.help_text, "", 0))

    # 2. Each subcommand's --help
    for cmd in tree.commands:
        full = f"{tree.binary} {cmd.name} --help"
        if dry_run:
            results.append(_make_result(full, -1, "[dry-run]", "", 0))
            continue
        results.append(_run(full, timeout))

    # 3. Boolean flags on subcommands (skip value-taking flags — we'd need to guess values)
    if not dry_run:
        for cmd in tree.commands:
            for opt in cmd.options:
                if not opt.takes_value:
                    full = f"{tree.binary} {cmd.name} {opt.flag}"
                    results.append(_run(full, timeout))

    # 4. Global boolean flags
    if not dry_run:
        for opt in tree.global_options:
            if not opt.takes_value and opt.flag not in ("--help", "--version"):
                full = f"{tree.binary} {opt.flag}"
                results.append(_run(full, timeout))

    return results


def _run(command: str, timeout: int) -> CommandResult:
    """Execute a single shell command and capture everything."""
    start = time.monotonic()
    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        elapsed = int((time.monotonic() - start) * 1000)
        return CommandResult(
            command=command,
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            duration_ms=elapsed,
            timed_out=False,
        )
    except subprocess.TimeoutExpired:
        elapsed = int((time.monotonic() - start) * 1000)
        return CommandResult(
            command=command,
            exit_code=-1,
            stdout="",
            stderr="TIMEOUT",
            duration_ms=elapsed,
            timed_out=True,
        )
    except FileNotFoundError:
        return CommandResult(
            command=command,
            exit_code=127,
            stdout="",
            stderr=f"Command not found: {command.split()[0]}",
            duration_ms=0,
            timed_out=False,
        )


def _make_result(
    command: str, exit_code: int, stdout: str, stderr: str, duration_ms: int
) -> CommandResult:
    return CommandResult(
        command=command,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_ms=duration_ms,
        timed_out=False,
    )
