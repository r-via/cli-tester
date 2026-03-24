"""Send probe results to Claude Code agent (opus) for adversarial analysis.

Uses Claude Code SDK — the agent can read/write files directly.
No JSON parsing needed: the agent writes improvements.md and patches code itself.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from parser import HelpTree
from runner import CommandResult

MODEL = "claude-opus-4-6"


def find_readme(binary: str) -> str | None:
    """Try to find and read a README file near the binary."""
    candidates = [Path(".")]
    binary_path = Path(binary.split()[-1])
    if binary_path.exists():
        candidates.insert(0, binary_path.parent)

    for base in candidates:
        for name in ("README.md", "README.rst", "README.txt", "README"):
            readme = base / name
            if readme.is_file():
                try:
                    return readme.read_text()
                except (UnicodeDecodeError, PermissionError):
                    continue
    return None


def get_current_improvement(improvements_path: Path) -> str | None:
    """Return the first unchecked improvement from improvements.md."""
    if not improvements_path.is_file():
        return None
    for line in improvements_path.read_text().splitlines():
        m = re.match(r"^- \[ \] (.+)$", line.strip())
        if m:
            return m.group(1)
    return None


def count_unchecked(improvements_path: Path) -> int:
    """Count unchecked items in improvements.md."""
    if not improvements_path.is_file():
        return 0
    return len(re.findall(r"^- \[ \]", improvements_path.read_text(), re.MULTILINE))


def count_checked(improvements_path: Path) -> int:
    """Count checked items in improvements.md."""
    if not improvements_path.is_file():
        return 0
    return len(re.findall(r"^- \[x\]", improvements_path.read_text(), re.MULTILINE))


def build_analysis_prompt(
    tree: HelpTree,
    results: list[CommandResult],
    binary: str,
    project_dir: Path,
    yolo: bool = False,
) -> str:
    """Build the prompt for Claude Code agent to analyze and act."""
    readme = find_readme(binary)
    improvements_path = project_dir / "improvements.md"
    improvements = improvements_path.read_text() if improvements_path.is_file() else None
    current = get_current_improvement(improvements_path)

    # Build probe results summary
    probe_lines = []
    for r in results:
        status = "OK" if r.ok else ("TIMEOUT" if r.timed_out else f"EXIT={r.exit_code}")
        probe_lines.append(f"  [{status}] {r.command} ({r.duration_ms}ms)")
        if not r.ok and r.stderr:
            probe_lines.append(f"    stderr: {r.stderr[:300]}")
    probe_summary = "\n".join(probe_lines)

    yolo_note = ""
    if not yolo:
        yolo_note = """
CONSTRAINT: Do NOT add new binaries or pip packages. If an improvement requires
a new dependency, add it to improvements.md with the tag [needs-package] and
leave it unchecked. The operator must re-run with --yolo to allow it."""

    readme_section = f"## README (specification)\n{readme}" if readme else "## README\n(no README found)"
    improvements_section = f"## improvements.md (current state)\n{improvements}" if improvements else "## improvements.md\n(does not exist yet — you must create it)"
    target_section = f"Current target improvement: {current}" if current else "No improvements yet — create initial improvements.md based on your analysis."

    return f"""\
You are an adversarial CLI tester working in {project_dir}.

{readme_section}

{improvements_section}

{target_section}

## Probe results (every command from --help was executed)
{probe_summary}

## Your task

1. If improvements.md does not exist, create it with improvements you identified.
   Each improvement must be a checkbox line:
   - [ ] [functional] description
   - [ ] [performance] description
   Improvements that need new packages: - [ ] [functional] [needs-package] description

2. If improvements.md exists, focus on the FIRST unchecked [ ] item.
   Read the source code, understand the issue, and fix it by editing the files directly.

3. After fixing, check off the improvement:
   Change "- [ ]" to "- [x]" for that line in improvements.md.

4. Do NOT touch already checked [x] items.
{yolo_note}

Work directly on the files. Do not ask questions. Do not explain — just fix."""


async def run_claude_agent(prompt: str, project_dir: Path) -> None:
    """Run Claude Code agent with the given prompt. It acts directly on files."""
    from claude_code_sdk import query, ClaudeCodeOptions, AssistantMessage, ResultMessage

    options = ClaudeCodeOptions(
        max_turns=20,
        permission_mode="bypassPermissions",
        model=MODEL,
        cwd=str(project_dir),
        disallowed_tools=["Task", "Agent", "WebSearch", "WebFetch"],
    )

    turn = 0
    stream = query(prompt=prompt, options=options)
    ait = stream.__aiter__()
    while True:
        try:
            message = await ait.__anext__()
        except StopAsyncIteration:
            break
        except Exception as e:
            print(f"  [sdk] error: {e}")
            continue

        msg_type = type(message).__name__
        if isinstance(message, (AssistantMessage, ResultMessage)):
            for block in message.content:
                if hasattr(block, "text") and block.text.strip():
                    print(f"  [opus] {block.text[:300]}")
                elif hasattr(block, "name"):
                    # Tool use block
                    print(f"  [opus] tool: {block.name}")
        else:
            turn += 1
            if turn <= 3:
                print(f"  [sdk] {msg_type}")

    print(f"  [opus] agent finished ({turn} events)")


def analyze_and_fix(
    tree: HelpTree,
    results: list[CommandResult],
    binary: str,
    project_dir: Path,
    yolo: bool = False,
) -> None:
    """Run Claude opus agent to analyze results and fix code directly."""
    try:
        from claude_code_sdk import query
    except ImportError:
        print("WARN: claude-code-sdk not installed, skipping agent analysis")
        return

    prompt = build_analysis_prompt(tree, results, binary, project_dir, yolo=yolo)

    try:
        asyncio.run(run_claude_agent(prompt, project_dir))
    except Exception as e:
        print(f"WARN: Claude Code agent failed ({e})")


def fallback_report(results: list[CommandResult]) -> dict:
    """Simple local analysis when Claude Code SDK is not available."""
    issues = []
    for r in results:
        if r.timed_out:
            issues.append(f"TIMEOUT: {r.command} ({r.duration_ms}ms)")
        elif r.exit_code != 0 and r.exit_code != -1:
            issues.append(f"EXIT {r.exit_code}: {r.command} — {r.stderr[:200]}")

    total = len(results)
    ok = sum(1 for r in results if r.ok)
    return {
        "total": total,
        "passed": ok,
        "failed": total - ok,
        "issues": issues,
    }
