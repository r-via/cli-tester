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


def _get_round_from_probe(project_dir: Path) -> int:
    """Find the latest probe_round_N.txt and return N."""
    runs_dir = project_dir / "runs"
    if not runs_dir.is_dir():
        return 0
    latest = 0
    for f in runs_dir.glob("probe_round_*.txt"):
        try:
            n = int(f.stem.split("_")[-1])
            latest = max(latest, n)
        except ValueError:
            pass
    return latest


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
    run_dir: Path | None = None,
) -> str:
    """Build the prompt for Claude Code agent to analyze and act."""
    readme = find_readme(binary)
    improvements_path = project_dir / "runs" / "improvements.md"
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

    # Include previous round's post-fix probe results if available
    prev_probe = ""
    prev_probe_path = project_dir / "runs" / f"probe_round_{max(1, _get_round_from_probe(project_dir))}.txt"
    if prev_probe_path.is_file():
        prev_probe = f"\n## Previous round post-fix probe results\n{prev_probe_path.read_text()}\n"

    return f"""\
You are an adversarial CLI tester working in {project_dir}.

{readme_section}

{improvements_section}

{target_section}
{prev_probe}
## Probe results (every command from --help was executed BEFORE your fixes)
These are the results from running every discovered command. After you make fixes,
the orchestrator will re-probe automatically to verify. You do NOT need to run the
full probe yourself — but you SHOULD run individual commands to verify specific fixes.
{probe_summary}

## CRITICAL RULE: errors first, improvements second

**Phase 1 — ERRORS (mandatory)**:
Before ANY improvement work, you MUST:
1. Run the CLI yourself: execute the binary with --help, then try key commands.
2. Check for Python errors, tracebacks, import errors, runtime crashes.
3. If ANY error exists in the console output (tracebacks, exceptions, exit codes != 0
   that indicate bugs), your ONLY job is to fix those errors. Do NOT work on improvements.
4. After fixing, re-run the command to verify the error is gone.
5. Repeat until there are ZERO errors.

Only when all commands run cleanly (no tracebacks, no crashes) may you proceed to Phase 2.

**Phase 2 — IMPROVEMENTS (only when zero errors)**:

IMPORTANT: Only ONE improvement per turn. Do not batch multiple improvements.

1. If runs/improvements.md does not exist, create it with a SINGLE improvement — the
   most impactful one you identified. Do NOT list multiple items upfront.
   Format:
   - [ ] [functional] description
   - [ ] [performance] description
   If it needs a new package: - [ ] [functional] [needs-package] description

2. If improvements.md exists and has an unchecked [ ] item, implement ONLY that one.
   Read the source code, understand the issue, and fix it by editing the files directly.

3. After fixing, verify the fix works by running the relevant command.

4. Only check off the improvement (change "- [ ]" to "- [x]") AFTER verifying it works.

5. Do NOT touch already checked [x] items.

6. After checking off the improvement, add exactly ONE new unchecked improvement
   as the next item — the most impactful remaining issue you see.
   Review the code against the README:
   - Does the CLI do everything the README promises?
   - Are there best practices missing? (error handling, input validation, edge cases)
   - Are there performance optimizations possible?
   - Is the code clean, maintainable, well-structured?
   If you see no further improvement needed, do NOT add one — proceed to Phase 3.
{yolo_note}

**Phase 3 — CONVERGENCE (only when everything is truly done)**:
You MUST only declare convergence when ALL of the following are true:
- Zero errors in console output
- All improvements.md checkboxes are checked
- The README specification is 100% fulfilled — every feature, command, and behavior it describes works
- Best practices are applied (error handling, input validation, edge cases)
- Performance is optimized where reasonable
- You cannot identify any further meaningful improvement

When you are certain, write a file `{run_dir or 'runs'}/CONVERGED` with a short summary of why you
believe the project has converged. Example:
  "README 100% fulfilled. All 12 improvements done. 100% probe pass rate. No further improvements identified."

Do NOT converge prematurely. If in doubt, add more improvements instead.

## Verification — MANDATORY for every action
- BEFORE starting work, read the run directory ({run_dir or 'runs'}) to check previous
  conversation logs, probe results, and any errors from earlier rounds. Learn from them.
- After EVERY file you write or edit, read it back to confirm it was written correctly.
- After EVERY command you run, check the full output for errors, warnings, or unexpected behavior.
- After editing improvements.md, read it back to verify the checkbox was toggled correctly.
- After writing any output file (reports, CONVERGED, etc.), read it back to confirm content.
- If any verification fails, fix it immediately before moving on.
- Show the full output of every command you run — do not truncate.
- Treat a failed verification the same as a console error: fix it before doing anything else.

Work directly on the files. Do not ask questions. Do not explain — just fix and verify."""


def _patch_sdk_parser():
    """Monkey-patch SDK to not crash on malformed rate_limit_event."""
    try:
        from claude_agent_sdk._internal import message_parser
        original = message_parser.parse_message
        def patched(data):
            try:
                return original(data)
            except Exception:
                if isinstance(data, dict) and data.get("type") == "rate_limit_event":
                    return None  # skip malformed rate limit events
                raise
        message_parser.parse_message = patched
    except Exception:
        pass


async def run_claude_agent(prompt: str, project_dir: Path, round_num: int = 1, run_dir: Path | None = None) -> None:
    """Run Claude Code agent with the given prompt. Logs conversation to run_dir/."""
    _patch_sdk_parser()
    from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ResultMessage

    options = ClaudeAgentOptions(
        max_turns=20,
        permission_mode="bypassPermissions",
        model=MODEL,
        cwd=str(project_dir),
        disallowed_tools=["Task", "Agent", "WebSearch", "WebFetch"],
        include_partial_messages=True,
    )

    # Prepare conversation log file in run directory
    out_dir = run_dir or (project_dir / "runs")
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / f"conversation_loop_{round_num}.md"
    log = open(log_path, "w")
    log.write(f"# Evolution Round {round_num}\n\n")

    def _log(line: str, console: bool = False):
        log.write(line + "\n")
        if console:
            print(line)

    turn = 0
    tools_used = 0
    stream = query(prompt=prompt, options=options)
    ait = stream.__aiter__()
    while True:
        try:
            message = await ait.__anext__()
        except StopAsyncIteration:
            break
        except Exception as e:
            _log(f"> SDK error: {e}")
            continue

        if message is None:
            continue

        msg_type = type(message).__name__
        turn += 1

        if msg_type == "StreamEvent":
            continue

        if isinstance(message, (AssistantMessage, ResultMessage)):
            if not hasattr(message, "content") or not message.content:
                continue
            for block in message.content:
                block_type = type(block).__name__

                if hasattr(block, "thinking"):
                    _log(f"\n### Thinking\n\n{block.thinking}\n")

                elif hasattr(block, "text") and block.text.strip():
                    _log(f"\n{block.text}\n", console=True)

                elif hasattr(block, "name"):
                    tools_used += 1
                    tool_name = block.name
                    tool_input = ""
                    if hasattr(block, "input") and block.input:
                        inp = block.input
                        if isinstance(inp, dict):
                            if "command" in inp:
                                tool_input = inp["command"]
                            elif "pattern" in inp:
                                tool_input = inp["pattern"]
                            elif "file_path" in inp:
                                tool_input = inp["file_path"]
                            elif "old_string" in inp:
                                tool_input = f'{inp.get("file_path", "?")} (edit)'
                            elif "content" in inp:
                                tool_input = f'({len(inp["content"])} chars)'
                        else:
                            tool_input = str(inp)[:100]
                    _log(f"\n**{tool_name}**: `{tool_input}`\n")
                    print(f"  [opus] {tool_name} → {tool_input[:80]}")

                elif block_type == "ToolResultBlock":
                    content_str = str(block.content)[:500] if hasattr(block, "content") and block.content else ""
                    is_error = getattr(block, "is_error", False)
                    if is_error:
                        _log(f"\n> ❌ Error:\n> {content_str}\n")
                    else:
                        _log(f"\n```\n{content_str}\n```\n")
        else:
            if msg_type == "RateLimitEvent":
                _log(f"\n> ⏳ Rate limited\n")
            elif msg_type == "SystemMessage":
                _log(f"\n---\n*Session initialized*\n---\n")

    _log(f"\n---\n\n**Done**: {turn} messages, {tools_used} tool calls\n")
    log.close()
    print(f"  [opus] done ({tools_used} tool calls) → {log_path}")


def analyze_and_fix(
    tree: HelpTree,
    results: list[CommandResult],
    binary: str,
    project_dir: Path,
    yolo: bool = False,
    max_retries: int = 5,
    round_num: int = 1,
    run_dir: Path | None = None,
) -> None:
    """Run Claude opus agent to analyze results and fix code directly."""
    try:
        from claude_agent_sdk import query
    except ImportError:
        print("WARN: claude-code-sdk not installed, skipping agent analysis")
        return

    prompt = build_analysis_prompt(tree, results, binary, project_dir, yolo=yolo, run_dir=run_dir)

    import warnings
    warnings.filterwarnings("ignore", message=".*cancel scope.*")
    warnings.filterwarnings("ignore", message=".*Event loop is closed.*")

    for attempt in range(1, max_retries + 1):
        try:
            asyncio.run(run_claude_agent(prompt, project_dir, round_num=round_num, run_dir=run_dir))
            return  # success
        except RuntimeError as e:
            if "cancel scope" in str(e) or "Event loop is closed" in str(e):
                return  # SDK cleanup noise — agent finished successfully
            if "rate_limit" in str(e).lower() and attempt < max_retries:
                wait = 60 * attempt
                print(f"  [sdk] rate limited — waiting {wait}s (attempt {attempt}/{max_retries})...")
                import time
                time.sleep(wait)
            else:
                print(f"WARN: Claude Code agent failed ({e})")
                return
        except Exception as e:
            if "rate_limit" in str(e).lower() and attempt < max_retries:
                wait = 60 * attempt
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
