"""Self-improving evolution loop.

Runs cli-probe against a target, sends the report to Claude with the source code,
asks Claude to generate patches, applies them, rebuilds, and repeats.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from parser import parse_help
from runner import run_all_commands
from analyzer import analyze_results
from report import generate_report, print_report

EVOLVE_PROMPT = """\
You are an expert CLI developer. You received a probe report for a CLI tool,
along with its source code. Your job is to fix the issues found.

Rules:
- Only fix real issues found in the report — do not refactor for style.
- Return a JSON array of patches, each with:
  {
    "file": "<relative path>",
    "description": "<what you're fixing>",
    "search": "<exact text to find>",
    "replace": "<replacement text>"
  }
- If the CLI is already good (score >= 9, no errors), return an empty array [].
- Keep patches minimal and focused.
"""


def evolve_loop(
    binary: str,
    max_rounds: int = 5,
    timeout: int = 10,
    target_dir: str | None = None,
) -> None:
    """Run probe → analyze → patch → rebuild loop until convergence."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY required for evolve mode", file=sys.stderr)
        sys.exit(1)

    try:
        import anthropic
    except ImportError:
        print("ERROR: pip install anthropic", file=sys.stderr)
        sys.exit(1)

    # Resolve source directory
    if target_dir:
        src_dir = Path(target_dir)
    else:
        # Try to find binary's source
        src_dir = _find_source_dir(binary)

    if not src_dir or not src_dir.is_dir():
        print(f"ERROR: Could not find source directory. Use --target-dir.", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    history: list[dict] = []

    for round_num in range(1, max_rounds + 1):
        print(f"\n{'#' * 60}")
        print(f"  EVOLUTION ROUND {round_num}/{max_rounds}")
        print(f"{'#' * 60}\n")

        # 1. Probe
        tree = parse_help(binary, timeout=timeout)
        if not tree:
            print(f"ERROR: Cannot parse --help for '{binary}'", file=sys.stderr)
            sys.exit(1)

        results = run_all_commands(tree, timeout=timeout)
        analysis = analyze_results(tree, results)
        report = generate_report(tree, results, analysis)
        print_report(report)

        history.append(report)

        # 2. Check convergence
        if analysis.get("converged", False):
            print(f"\n*** CONVERGED at round {round_num} ***")
            print(f"Score: {analysis.get('score', '?')}/10")
            _save_evolution_log(history, binary)
            return

        # 3. Gather source code
        source_context = _read_source_files(src_dir)

        # 4. Ask Claude for patches
        patches = _get_patches(client, report, source_context, round_num)

        if not patches:
            print(f"\nNo patches suggested — stopping.")
            _save_evolution_log(history, binary)
            return

        # 5. Apply patches
        applied = _apply_patches(patches, src_dir)
        print(f"\nApplied {applied}/{len(patches)} patches.")

        # 6. Rebuild if needed
        _rebuild(src_dir)

    print(f"\n*** Max rounds ({max_rounds}) reached without convergence ***")
    _save_evolution_log(history, binary)


def _find_source_dir(binary: str) -> Path | None:
    """Try to find where the binary's source lives."""
    # If binary is a path, use its parent
    p = Path(binary)
    if p.exists():
        return p.parent
    # Try `which`
    try:
        result = subprocess.run(["which", binary], capture_output=True, text=True)
        if result.returncode == 0:
            return Path(result.stdout.strip()).parent
    except FileNotFoundError:
        pass
    return None


def _read_source_files(src_dir: Path) -> str:
    """Read all Python/TS/JS source files from the directory."""
    lines = []
    extensions = {".py", ".ts", ".js", ".sh"}
    for f in sorted(src_dir.rglob("*")):
        if f.is_file() and f.suffix in extensions and "node_modules" not in str(f):
            try:
                content = f.read_text()
                lines.append(f"### {f.relative_to(src_dir)}")
                lines.append(f"```{f.suffix.lstrip('.')}")
                lines.append(content)
                lines.append("```\n")
            except (UnicodeDecodeError, PermissionError):
                continue
    return "\n".join(lines)


def _get_patches(
    client,
    report: dict,
    source_context: str,
    round_num: int,
) -> list[dict]:
    """Ask Claude to generate patches based on the probe report."""
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": (
                    f"{EVOLVE_PROMPT}\n\n"
                    f"## Round {round_num}\n\n"
                    f"## Probe Report\n```json\n{json.dumps(report, indent=2)}\n```\n\n"
                    f"## Source Code\n{source_context}"
                ),
            }
        ],
    )

    text = message.content[0].text
    try:
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]
        patches = json.loads(text.strip())
        return patches if isinstance(patches, list) else []
    except (json.JSONDecodeError, IndexError):
        print(f"WARN: Could not parse patches from Claude response")
        return []


def _apply_patches(patches: list[dict], src_dir: Path) -> int:
    """Apply search/replace patches to files. Returns count of successful patches."""
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


def _rebuild(src_dir: Path) -> None:
    """Try to rebuild the project if a build step is detected."""
    # Python: nothing to build
    # Node: try npm run build
    if (src_dir / "package.json").exists():
        print("Rebuilding (npm run build)...")
        subprocess.run(["npm", "run", "build"], cwd=src_dir, capture_output=True)
    # Rust: cargo build
    elif (src_dir / "Cargo.toml").exists():
        print("Rebuilding (cargo build)...")
        subprocess.run(["cargo", "build"], cwd=src_dir, capture_output=True)


def _save_evolution_log(history: list[dict], binary: str) -> None:
    """Save the full evolution history to runs/ directory."""
    from datetime import datetime
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
