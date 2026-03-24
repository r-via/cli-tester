"""Self-improving evolution loop.

Each round runs as a **separate subprocess** so that code changes
made by opus in round N are picked up by round N+1.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from analyzer import count_checked, count_unchecked, get_current_improvement

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text
    _RICH = True
    _console = Console()
except ImportError:
    _RICH = False
    _console = None


def evolve_loop(
    binary: str,
    max_rounds: int = 5,
    timeout: int = 10,
    target_dir: str | None = None,
    yolo: bool = False,
) -> None:
    """Orchestrate evolution by launching each round as a subprocess."""
    src_dir = _resolve_src_dir(binary, target_dir)
    improvements_path = src_dir / "improvements.md"

    # Ensure git
    _ensure_git(src_dir)

    for round_num in range(1, max_rounds + 1):
        current = get_current_improvement(improvements_path)
        checked = count_checked(improvements_path)
        unchecked = count_unchecked(improvements_path)

        if _RICH:
            content = Text()
            content.append(f"EVOLUTION ROUND {round_num}/{max_rounds}\n", style="bold white")
            if current:
                content.append(f"TARGET: ", style="bold")
                content.append(f"{current}\n", style="cyan")
                content.append(f"PROGRESS: ", style="bold")
                content.append(f"{checked}/{checked + unchecked} improvements done", style="green")
            else:
                content.append("TARGET: (initial analysis)", style="yellow")
            _console.print()
            _console.print(Panel(content, border_style="magenta", title="[bold magenta]evolve[/bold magenta]"))
        else:
            print(f"\n{'#' * 60}")
            print(f"  EVOLUTION ROUND {round_num}/{max_rounds}")
            if current:
                print(f"  TARGET: {current}")
                print(f"  PROGRESS: {checked}/{checked + unchecked} improvements done")
            else:
                print(f"  TARGET: (initial analysis)")
            print(f"{'#' * 60}")

        # Launch round as subprocess — picks up any code changes from previous round
        cmd = [
            sys.executable, str(Path(__file__).parent / "cli_tester.py"),
            "_round",
            binary,
            "--round-num", str(round_num),
            "--timeout", str(timeout),
        ]
        if target_dir:
            cmd += ["--target-dir", target_dir]
        if yolo:
            cmd += ["--yolo"]

        result = subprocess.run(cmd, cwd=str(src_dir))

        if result.returncode != 0:
            if _RICH:
                _console.print(f"\n  [bold red]Round {round_num} failed[/bold red] (exit {result.returncode})")
            else:
                print(f"\n  Round {round_num} failed (exit {result.returncode})")

        # Re-read improvements after subprocess ran (code may have changed)
        unchecked = count_unchecked(improvements_path)
        checked = count_checked(improvements_path)

        if _RICH:
            _console.print(f"\n  Progress: [green]{checked} done[/green], [yellow]{unchecked} remaining[/yellow]")
        else:
            print(f"\n  Progress: {checked} done, {unchecked} remaining")

        # Check if agent wrote CONVERGED marker
        converged_path = src_dir / "runs" / "CONVERGED"
        if converged_path.is_file():
            reason = converged_path.read_text().strip()
            print(f"\n*** CONVERGED at round {round_num} ***")
            print(f"  {reason}")
            converged_path.unlink()  # clean up marker
            return

    unchecked = count_unchecked(improvements_path)
    checked = count_checked(improvements_path)
    if _RICH:
        _console.print(f"\n[bold yellow]*** Max rounds ({max_rounds}) reached — {checked} done, {unchecked} remaining ***[/bold yellow]")
    else:
        print(f"\n*** Max rounds ({max_rounds}) reached — {checked} done, {unchecked} remaining ***")


def run_single_round(
    binary: str,
    round_num: int,
    timeout: int = 10,
    target_dir: str | None = None,
    yolo: bool = False,
) -> None:
    """Execute a single evolution round (called as subprocess)."""
    from parser import parse_help
    from runner import run_all_commands
    from analyzer import analyze_and_fix, get_current_improvement
    from report import print_probe_summary

    src_dir = _resolve_src_dir(binary, target_dir)
    improvements_path = src_dir / "improvements.md"

    # 1. Probe
    tree = parse_help(binary, timeout=timeout)
    if not tree:
        print(f"ERROR: Cannot parse --help for '{binary}'", file=sys.stderr)
        sys.exit(1)

    results = run_all_commands(tree, timeout=timeout)
    print_probe_summary(results)

    # 2. Let Claude opus agent analyze and fix
    current = get_current_improvement(improvements_path)
    print(f"\n  [agent] Claude opus working...")
    analyze_and_fix(tree, results, binary, src_dir, yolo=yolo, round_num=round_num)

    # 3. Git commit
    new_current = get_current_improvement(improvements_path)
    if current and new_current != current:
        _git_commit(src_dir, f"evolve: ✓ {current}")
    else:
        _git_commit(src_dir, f"evolve: round {round_num}")


def _resolve_src_dir(binary: str, target_dir: str | None) -> Path:
    if target_dir:
        src_dir = Path(target_dir)
    else:
        src_dir = _find_source_dir(binary)
    if not src_dir or not src_dir.is_dir():
        print(f"ERROR: Could not find source directory. Use --target-dir.", file=sys.stderr)
        sys.exit(1)
    return src_dir


def _find_source_dir(binary: str) -> Path | None:
    p = Path(binary)
    if p.exists():
        return p.parent
    parts = binary.split()
    if len(parts) > 1:
        p = Path(parts[-1])
        if p.exists():
            return p.parent
    try:
        result = subprocess.run(["which", binary], capture_output=True, text=True)
        if result.returncode == 0:
            return Path(result.stdout.strip()).parent
    except FileNotFoundError:
        pass
    return None


def _ensure_git(src_dir: Path) -> None:
    result = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=src_dir, capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"ERROR: {src_dir} is not a git repository.", file=sys.stderr)
        sys.exit(1)

    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=src_dir, capture_output=True, text=True,
    )
    if status.stdout.strip():
        print("Uncommitted changes — committing snapshot...")
        subprocess.run(["git", "add", "-A"], cwd=src_dir)
        subprocess.run(
            ["git", "commit", "-m", "evolve: snapshot before evolution"],
            cwd=src_dir, capture_output=True,
        )


def _git_commit(src_dir: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=src_dir)
    status = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=src_dir)
    if status.returncode == 0:
        print(f"  [git] no changes")
        return
    subprocess.run(["git", "commit", "-m", message], cwd=src_dir, capture_output=True)
    print(f"  [git] {message}")
