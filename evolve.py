"""Self-improving evolution loop.

Each round:
1. Probe the CLI (run every command)
2. Analyze with opus (README + improvements.md as convergence spec)
3. If current improvement achieved → check it off, git commit
4. If new improvements discovered → append to improvements.md
5. Generate patches → apply → rebuild → git commit
6. Loop until all improvements checked AND score >= 9
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from parser import parse_help
from runner import run_all_commands
from analyzer import (
    analyze_results,
    _query_claude,
    append_improvements,
    check_improvement,
    get_current_improvement,
    find_improvements,
    MODEL,
)
from report import generate_report, print_report

EVOLVE_PROMPT = """\
You are an expert CLI developer using Claude opus. You received a probe report
for a CLI tool, along with its source code, README, and improvements.md.

Your current goal is to implement or fix the FIRST unchecked improvement
from improvements.md. Focus on that single item.

Rules:
- Only change code to address the current improvement or fix probe errors.
- Do NOT add new binaries or packages. If you must, set "needs_package": true
  and the patch will be skipped (operator must re-run with --yolo).
- Return a JSON object:
  {
    "patches": [
      {
        "file": "<relative path>",
        "description": "<what you're fixing>",
        "search": "<exact text to find>",
        "replace": "<replacement text>",
        "needs_package": false
      }
    ],
    "improvement_done": <true if this round completes the current improvement>
  }
- If no patches needed, return {"patches": [], "improvement_done": false}.
"""


def evolve_loop(
    binary: str,
    max_rounds: int = 5,
    timeout: int = 10,
    target_dir: str | None = None,
    yolo: bool = False,
) -> None:
    """Run probe → analyze → patch → rebuild loop until convergence."""
    try:
        from claude_code_sdk import query, ClaudeCodeOptions
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

    history: list[dict] = []

    for round_num in range(1, max_rounds + 1):
        print(f"\n{'#' * 60}")
        print(f"  EVOLUTION ROUND {round_num}/{max_rounds}")
        current = get_current_improvement(
            improvements_path.read_text() if improvements_path.is_file() else None
        )
        if current:
            print(f"  TARGET: {current}")
        else:
            print(f"  TARGET: (initial analysis)")
        print(f"{'#' * 60}\n")

        # 1. Probe
        tree = parse_help(binary, timeout=timeout)
        if not tree:
            print(f"ERROR: Cannot parse --help for '{binary}'", file=sys.stderr)
            sys.exit(1)

        results = run_all_commands(tree, timeout=timeout)
        analysis = analyze_results(tree, results, binary=binary)
        report = generate_report(tree, results, analysis)
        print_report(report)

        history.append(report)

        # 2. Handle new improvements from analysis
        new_improvements = analysis.get("new_improvements", [])
        if new_improvements:
            # Filter out needs_package unless --yolo
            if not yolo:
                blocked = [i for i in new_improvements if i.get("needs_package")]
                new_improvements = [i for i in new_improvements if not i.get("needs_package")]
                for b in blocked:
                    print(f"  BLOCKED: '{b['text']}' needs a package — use --yolo to allow")

            append_improvements(improvements_path, new_improvements)
            print(f"  Added {len(new_improvements)} improvements to improvements.md")

        # 3. Check if current improvement was achieved
        if analysis.get("improvement_achieved") and current:
            check_improvement(improvements_path, current)
            print(f"  ✓ DONE: {current}")
            _git_commit(src_dir, round_num, f"evolve: ✓ {current}")

        # 4. Check full convergence
        remaining = _count_unchecked(improvements_path)
        if analysis.get("converged", False) and remaining == 0:
            print(f"\n*** CONVERGED at round {round_num} ***")
            print(f"Score: {analysis.get('score', '?')}/10")
            print(f"All improvements checked!")
            _save_evolution_log(history, binary)
            return

        # 5. Get patches for current improvement
        source_context = _read_source_files(src_dir)
        readme = (src_dir / "README.md").read_text() if (src_dir / "README.md").is_file() else ""
        improv_text = improvements_path.read_text() if improvements_path.is_file() else ""

        patch_result = asyncio.run(_get_patches(
            report, source_context, readme, improv_text, round_num, yolo=yolo,
        ))

        patches = patch_result.get("patches", [])

        # Filter needs_package patches unless --yolo
        if not yolo:
            blocked_patches = [p for p in patches if p.get("needs_package")]
            patches = [p for p in patches if not p.get("needs_package")]
            for bp in blocked_patches:
                print(f"  BLOCKED PATCH: '{bp['description']}' needs a package — use --yolo")

        if not patches:
            if remaining == 0:
                print(f"\nNo patches needed — all done!")
            else:
                print(f"\nNo patches suggested — {remaining} improvements remaining.")
            _save_evolution_log(history, binary)
            if remaining == 0:
                return
            continue

        # 6. Apply patches
        applied = _apply_patches(patches, src_dir)
        print(f"\nApplied {applied}/{len(patches)} patches.")

        # 7. Rebuild
        _rebuild(src_dir)

        # 8. Git commit
        desc = current or "fixes"
        _git_commit(src_dir, round_num, f"evolve: round {round_num} — {desc}")

        # 9. If patch_result says improvement is done, check it off
        if patch_result.get("improvement_done") and current:
            check_improvement(improvements_path, current)
            print(f"  ✓ DONE: {current}")
            _git_commit(src_dir, round_num, f"evolve: ✓ {current}")

    remaining = _count_unchecked(improvements_path)
    print(f"\n*** Max rounds ({max_rounds}) reached — {remaining} improvements remaining ***")
    _save_evolution_log(history, binary)


def _count_unchecked(improvements_path: Path) -> int:
    """Count unchecked items in improvements.md."""
    if not improvements_path.is_file():
        return 0
    content = improvements_path.read_text()
    return len(re.findall(r"^- \[ \]", content, re.MULTILINE))


def _find_source_dir(binary: str) -> Path | None:
    """Try to find where the binary's source lives."""
    p = Path(binary)
    if p.exists():
        return p.parent
    # handle "python3 script.py"
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


def _read_source_files(src_dir: Path) -> str:
    """Read all source files from the directory."""
    lines = []
    extensions = {".py", ".ts", ".js", ".sh", ".rs", ".go"}
    skip_dirs = {"node_modules", ".venv", "__pycache__", ".git", "runs"}
    for f in sorted(src_dir.rglob("*")):
        if f.is_file() and f.suffix in extensions and not any(d in f.parts for d in skip_dirs):
            try:
                content = f.read_text()
                lines.append(f"### {f.relative_to(src_dir)}")
                lines.append(f"```{f.suffix.lstrip('.')}")
                lines.append(content)
                lines.append("```\n")
            except (UnicodeDecodeError, PermissionError):
                continue
    return "\n".join(lines)


async def _get_patches(
    report: dict,
    source_context: str,
    readme: str,
    improvements: str,
    round_num: int,
    yolo: bool = False,
) -> dict:
    """Ask Claude opus to generate patches for the current improvement."""
    yolo_note = "" if not yolo else "\nNote: --yolo mode is active. You MAY add new packages.\n"

    prompt = (
        f"{EVOLVE_PROMPT}\n{yolo_note}\n"
        f"## Round {round_num}\n\n"
        f"## README\n{readme}\n\n"
        f"## improvements.md\n{improvements}\n\n"
        f"## Probe Report\n```json\n{json.dumps(report, indent=2)}\n```\n\n"
        f"## Source Code\n{source_context}"
    )

    text = await _query_claude(prompt)
    try:
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]
        result = json.loads(text.strip())
        return result if isinstance(result, dict) else {"patches": [], "improvement_done": False}
    except (json.JSONDecodeError, IndexError):
        print(f"WARN: Could not parse patches from Claude response")
        return {"patches": [], "improvement_done": False}


def _apply_patches(patches: list[dict], src_dir: Path) -> int:
    """Apply search/replace patches to files."""
    applied = 0
    for patch in patches:
        filepath = src_dir / patch["file"]
        if not filepath.is_file():
            print(f"  SKIP: {patch['file']} not found")
            continue

        content = filepath.read_text()
        search = patch["search"]
        replace = patch["replace"]

        if search not in content:
            print(f"  SKIP: search text not found in {patch['file']}")
            continue

        new_content = content.replace(search, replace, 1)
        filepath.write_text(new_content)
        print(f"  PATCH: {patch['description']}")
        applied += 1

    return applied


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


def _git_commit(src_dir: Path, round_num: int, message: str) -> None:
    """Commit all changes with a descriptive message."""
    subprocess.run(["git", "add", "-A"], cwd=src_dir)

    status = subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=src_dir,
    )
    if status.returncode == 0:
        return

    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=src_dir, capture_output=True,
    )
    print(f"  [git] {message}")


def _rebuild(src_dir: Path) -> None:
    """Try to rebuild the project if a build step is detected."""
    if (src_dir / "package.json").exists():
        print("Rebuilding (npm run build)...")
        subprocess.run(["npm", "run", "build"], cwd=src_dir, capture_output=True)
    elif (src_dir / "Cargo.toml").exists():
        print("Rebuilding (cargo build)...")
        subprocess.run(["cargo", "build"], cwd=src_dir, capture_output=True)


def _save_evolution_log(history: list[dict], binary: str) -> None:
    """Save the full evolution history to runs/."""
    runs_dir = Path(__file__).parent / "runs"
    runs_dir.mkdir(exist_ok=True)
    safe_name = binary.replace("/", "_").replace(" ", "_")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = runs_dir / f"evolve_{safe_name}_{timestamp}.json"
    with open(filename, "w") as f:
        json.dump(
            {
                "binary": binary,
                "rounds": len(history),
                "final_score": history[-1].get("analysis", {}).get("score"),
                "history": history,
            },
            f,
            indent=2,
        )
    print(f"\nEvolution log saved to {filename}")
