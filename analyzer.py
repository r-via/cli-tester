"""Send probe results to Claude Code agent (opus) for adversarial analysis."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from parser import HelpTree
from runner import CommandResult

MODEL = "claude-opus-4-6"

ANALYSIS_PROMPT = """\
You are an adversarial CLI tester. You received:
- The project's README: the **specification** of what the CLI should do.
- The project's improvements.md: a checklist of planned improvements with checkboxes.
- The probe results: what actually happened when every command was executed.

Your job:
1. Compare README promises vs actual probe results. Flag every gap.
2. Check improvements.md — identify the FIRST unchecked ([ ]) improvement.
3. Evaluate whether that specific improvement has been achieved based on the probe.
4. Identify crashes, timeouts, inconsistencies, security concerns.
5. Rate quality 1-10 based on README conformance + improvements progress.

IMPORTANT constraints:
- Do NOT suggest adding new binaries or packages. If an improvement requires a new
  dependency, mark it with "needs_package": true so the operator can use --yolo.
- Improvements are either "functional" or "performance" — tag each one.

Return JSON:
{
  "score": <1-10>,
  "current_improvement": "<text of the first unchecked improvement, or null if all done>",
  "improvement_achieved": <true if current improvement is verified by probes>,
  "issues": [
    {"severity": "error|warning|info", "command": "<cmd>", "message": "<what's wrong>"}
  ],
  "new_improvements": [
    {"text": "<improvement>", "type": "functional|performance", "needs_package": false}
  ],
  "suggestions": ["<suggestion>"],
  "converged": <true if score >= 9 AND current_improvement is achieved AND no errors>
}
"""


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


def find_improvements(binary: str) -> str | None:
    """Try to find and read improvements.md near the binary."""
    candidates = [Path(".")]
    binary_path = Path(binary.split()[-1])
    if binary_path.exists():
        candidates.insert(0, binary_path.parent)

    for base in candidates:
        path = base / "improvements.md"
        if path.is_file():
            try:
                return path.read_text()
            except (UnicodeDecodeError, PermissionError):
                continue
    return None


def get_current_improvement(improvements_text: str | None) -> str | None:
    """Return the first unchecked improvement from improvements.md."""
    if not improvements_text:
        return None
    for line in improvements_text.splitlines():
        m = re.match(r"^- \[ \] (.+)$", line.strip())
        if m:
            return m.group(1)
    return None


def check_improvement(improvements_path: Path, improvement_text: str) -> None:
    """Check off an improvement in improvements.md."""
    if not improvements_path.is_file():
        return
    content = improvements_path.read_text()
    old = f"- [ ] {improvement_text}"
    new = f"- [x] {improvement_text}"
    if old in content:
        improvements_path.write_text(content.replace(old, new, 1))


def append_improvements(improvements_path: Path, new_items: list[dict]) -> None:
    """Append new improvements to improvements.md, skipping duplicates."""
    if not new_items:
        return

    existing = ""
    if improvements_path.is_file():
        existing = improvements_path.read_text()
    else:
        existing = "# Improvements\n\n"

    for item in new_items:
        text = item["text"]
        itype = item.get("type", "functional")
        needs_pkg = item.get("needs_package", False)

        # Skip if already present
        if text in existing:
            continue

        tag = f"[{itype}]"
        if needs_pkg:
            tag += " [needs-package]"

        line = f"- [ ] {tag} {text}\n"
        existing += line

    improvements_path.write_text(existing)


def analyze_results(
    tree: HelpTree,
    results: list[CommandResult],
    binary: str | None = None,
) -> dict:
    """Ask Claude Code agent (opus) to analyze probe results adversarially."""
    try:
        from claude_code_sdk import query, ClaudeCodeOptions
    except ImportError:
        print("WARN: claude-code-sdk not installed, using fallback analysis")
        return _fallback_analysis(results)

    bin_name = binary or tree.binary
    readme = find_readme(bin_name)
    improvements = find_improvements(bin_name)
    context = _build_context(tree, results, readme, improvements)
    prompt = f"{ANALYSIS_PROMPT}\n\n---\n\n{context}"

    try:
        result = asyncio.run(_query_claude(prompt))
        return _parse_response(result)
    except Exception as e:
        print(f"WARN: Claude Code query failed ({e}), using fallback analysis")
        return _fallback_analysis(results)


async def _query_claude(prompt: str, system: str | None = None) -> str:
    """Send a prompt to Claude Code agent and collect the response."""
    from claude_code_sdk import query, ClaudeCodeOptions, AssistantMessage, ResultMessage

    options = ClaudeCodeOptions(
        max_turns=1,
        system_prompt=system or ANALYSIS_PROMPT,
        permission_mode="bypassPermissions",
        model=MODEL,
    )

    response_text = ""
    stream = query(prompt=prompt, options=options)
    ait = stream.__aiter__()
    while True:
        try:
            message = await ait.__anext__()
        except StopAsyncIteration:
            break
        except Exception:
            continue

        if isinstance(message, (AssistantMessage, ResultMessage)):
            for block in message.content:
                if hasattr(block, "text"):
                    response_text += block.text

    return response_text


def _parse_response(response_text: str) -> dict:
    """Extract JSON from Claude's response."""
    try:
        text = response_text
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]
        return json.loads(text.strip())
    except (json.JSONDecodeError, IndexError):
        return {
            "score": 0,
            "issues": [{"severity": "error", "command": "N/A", "message": "Failed to parse Claude response"}],
            "suggestions": [],
            "converged": False,
            "raw_response": response_text,
        }


def _build_context(
    tree: HelpTree,
    results: list[CommandResult],
    readme: str | None = None,
    improvements: str | None = None,
) -> str:
    """Build context for Claude: README + improvements + probe results."""
    lines = [
        f"# CLI: {tree.binary}",
        f"## Commands discovered: {len(tree.commands)}",
        f"## Total probes run: {len(results)}",
    ]

    if readme:
        lines += ["", "## README (project specification)", readme]

    if improvements:
        lines += ["", "## improvements.md (improvement checklist)", improvements]
    else:
        lines += ["", "## improvements.md", "(not yet created — generate initial improvements)"]

    lines += ["", "## Probe Results:"]
    for r in results:
        status = "OK" if r.ok else ("TIMEOUT" if r.timed_out else f"EXIT={r.exit_code}")
        lines.append(f"  [{status}] {r.command} ({r.duration_ms}ms)")
        if not r.ok and r.stderr:
            stderr_short = r.stderr[:300].replace("\n", " ")
            lines.append(f"    stderr: {stderr_short}")

    return "\n".join(lines)


def _fallback_analysis(results: list[CommandResult]) -> dict:
    """Simple local analysis when Claude Code SDK is not available."""
    issues = []
    for r in results:
        if r.timed_out:
            issues.append({
                "severity": "error",
                "command": r.command,
                "message": f"Timed out after {r.duration_ms}ms",
            })
        elif r.exit_code != 0 and r.exit_code != -1:
            issues.append({
                "severity": "warning" if r.exit_code == 1 else "error",
                "command": r.command,
                "message": f"Exit code {r.exit_code}: {r.stderr[:200]}",
            })

    total = len(results)
    ok = sum(1 for r in results if r.ok)
    score = int((ok / total) * 10) if total else 0

    return {
        "score": score,
        "issues": issues,
        "current_improvement": None,
        "improvement_achieved": False,
        "new_improvements": [],
        "suggestions": ["Install claude-code-sdk for deeper analysis: pip install claude-code-sdk"],
        "converged": score >= 9 and not any(i["severity"] == "error" for i in issues),
    }
