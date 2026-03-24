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
            err_str = str(e)
            if "unknown message type" in err_str.lower():
                # SDK can't parse this event type (e.g. rate_limit_event)
                # It's not a real error — just an event the SDK doesn't know about
                # The stream may still be alive, keep going
                print(f"  [sdk] unknown event (ignoring): {err_str}")
                continue
            if "rate_limit" in err_str.lower():
                print(f"  [sdk] rate limited — raising for retry...")
                raise RuntimeError("rate_limit") from e
            print(f"  [sdk] error: {e}")
            continue

        msg_type = type(message).__name__
        turn += 1

        if isinstance(message, (AssistantMessage, ResultMessage)):
            for block in message.content:
                block_type = type(block).__name__
                if hasattr(block, "text") and block.text.strip():
                    print(f"  [opus] {block.text[:300]}")
                elif hasattr(block, "name"):
                    # Tool use block — show tool name + input summary
                    tool_name = block.name
                    tool_input = ""
                    if hasattr(block, "input") and block.input:
                        inp = block.input
                        if isinstance(inp, dict):
                            # Show key details depending on tool
                            if "command" in inp:
                                tool_input = f" → {inp['command'][:100]}"
                            elif "pattern" in inp:
                                tool_input = f" → {inp['pattern']}"
                            elif "file_path" in inp:
                                tool_input = f" → {inp['file_path']}"
                            elif "content" in inp:
                                tool_input = f" → ({len(inp['content'])} chars)"
                            else:
                                keys = list(inp.keys())[:3]
                                tool_input = f" → {keys}"
                        else:
                            tool_input = f" → {str(inp)[:80]}"
                    print(f"  [opus] {tool_name}{tool_input}")
                elif block_type == "ToolResultBlock":
                    # Tool result — show truncated output
                    if hasattr(block, "content") and block.content:
                        content_str = str(block.content)[:150]
                        print(f"  [result] {content_str}")
                else:
                    print(f"  [opus] {block_type}: {str(block)[:150]}")
        else:
            # System/User messages from SDK
            if hasattr(message, "content") and message.content:
                content = str(message.content)[:200] if not isinstance(message.content, str) else message.content[:200]
                print(f"  [{msg_type}] {content}")
            elif hasattr(message, "data") and message.data:
                # SystemMessage init etc
                subtype = message.data.get("subtype", "") if isinstance(message.data, dict) else ""
                print(f"  [{msg_type}] {subtype}")
            else:
                print(f"  [{msg_type}]")

    print(f"  [opus] agent done ({turn} messages)")


def analyze_and_fix(
    tree: HelpTree,
    results: list[CommandResult],
    binary: str,
    project_dir: Path,
    yolo: bool = False,
    max_retries: int = 3,
) -> None:
    """Run Claude opus agent to analyze results and fix code directly."""
    try:
        from claude_code_sdk import query
    except ImportError:
        print("WARN: claude-code-sdk not installed, skipping agent analysis")
        return

    prompt = build_analysis_prompt(tree, results, binary, project_dir, yolo=yolo)

    for attempt in range(1, max_retries + 1):
        try:
            asyncio.run(run_claude_agent(prompt, project_dir))
            return  # success
        except Exception as e:
            if "rate_limit" in str(e).lower() and attempt < max_retries:
                wait = 30 * attempt
                print(f"  [sdk] rate limited — waiting {wait}s (attempt {attempt}/{max_retries})...")
                import time
                time.sleep(wait)
            else:
                print(f"WARN: Claude Code agent failed ({e})")
                return


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
