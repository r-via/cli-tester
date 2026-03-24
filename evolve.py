"""Self-improving evolution loop.

Each round:
1. Probe the CLI (run every command)
2. Claude opus agent analyzes (README + improvements.md) and edits code directly
3. Git commit the changes
4. Check if current improvement was completed
5. Loop until all improvements checked AND probes pass
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

from parser import parse_help
from runner import run_all_commands
from analyzer import (
    analyze_and_fix,
    count_checked,
    count_unchecked,
    get_current_improvement,
    fallback_report,
)
from report import print_probe_summary


def evolve_loop(
    binary: str,
    max_rounds: int = 5,
    timeout: int = 10,
    target_dir: str | None = None,
    yolo: bool = False,
) -> None:
    """Run probe → agent fix → git commit loop until convergence."""
    try:
        from claude_agent_sdk import query
    except ImportError:
        print("ERROR: pip install claude-code-sdk", file=sys.stderr)
        sys.exit(1)

    # Resolve source directory
    if target_dir:
        src_dir = Path(target_dir)
    else:
        src_dir = _find_source_dir(binary)

    if not src_dir or not src_dir.is_dir():
        print(f"ERROR: Could not find source directory. Use --target-dir.", file=sys.stderr)
        sys.exit(1)

    improvements_path = src_dir / "improvements.md"

    # Ensure git
    _ensure_git(src_dir)

    for round_num in range(1, max_rounds + 1):
        current = get_current_improvement(improvements_path)
        checked = count_checked(improvements_path)
        unchecked = count_unchecked(improvements_path)

        print(f"\n{'#' * 60}")
        print(f"  EVOLUTION ROUND {round_num}/{max_rounds}")
        if current:
            print(f"  TARGET: {current}")
            print(f"  PROGRESS: {checked}/{checked + unchecked} improvements done")
        else:
            print(f"  TARGET: (initial analysis)")
        print(f"{'#' * 60}\n")

        # 1. Probe
        tree = parse_help(binary, timeout=timeout)
        if not tree:
            print(f"ERROR: Cannot parse --help for '{binary}'", file=sys.stderr)
            sys.exit(1)

        results = run_all_commands(tree, timeout=timeout)
        print_probe_summary(results)

        # 2. Let Claude opus agent analyze and fix
        print(f"\n  [agent] Claude opus analyzing and fixing...")
        analyze_and_fix(tree, results, binary, src_dir, yolo=yolo)

        # 3. Git commit whatever the agent changed
        new_current = get_current_improvement(improvements_path)
        if current and new_current != current:
            # The agent checked off the current improvement
            _git_commit(src_dir, f"evolve: ✓ {current}")
        else:
            _git_commit(src_dir, f"evolve: round {round_num}")

        # 4. Check convergence
        unchecked = count_unchecked(improvements_path)
        checked = count_checked(improvements_path)

        if unchecked == 0 and checked > 0:
            # All done — do a final probe to verify
            print(f"\n  All {checked} improvements checked. Final verification probe...")
            tree = parse_help(binary, timeout=timeout)
            if tree:
                final_results = run_all_commands(tree, timeout=timeout)
                ok = sum(1 for r in final_results if r.ok)
                total = len(final_results)
                print_probe_summary(final_results)
                if ok == total:
                    print(f"\n*** CONVERGED at round {round_num} ***")
                    print(f"  {checked} improvements completed, {ok}/{total} probes pass")
                    return
                else:
                    print(f"\n  {total - ok} probes still failing — continuing...")

    unchecked = count_unchecked(improvements_path)
    checked = count_checked(improvements_path)
    print(f"\n*** Max rounds ({max_rounds}) reached ***")
    print(f"  {checked} done, {unchecked} remaining")


def _find_source_dir(binary: str) -> Path | None:
    """Try to find where the binary's source lives."""
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
    """Ensure the source directory is a git repo with a clean state."""
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
        print("Uncommitted changes — committing snapshot before evolve...")
        subprocess.run(["git", "add", "-A"], cwd=src_dir)
        subprocess.run(
            ["git", "commit", "-m", "evolve: snapshot before evolution loop"],
            cwd=src_dir, capture_output=True,
        )


def _git_commit(src_dir: Path, message: str) -> None:
    """Commit all changes with a descriptive message."""
    subprocess.run(["git", "add", "-A"], cwd=src_dir)
    status = subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=src_dir,
    )
    if status.returncode == 0:
        print(f"  [git] no changes to commit")
        return

    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=src_dir, capture_output=True,
    )
    print(f"  [git] {message}")
