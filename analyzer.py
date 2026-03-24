"""Send probe results to Claude for adversarial analysis."""

from __future__ import annotations

import json
import os
from dataclasses import asdict

from parser import HelpTree
from runner import CommandResult

ANALYSIS_PROMPT = """\
You are an adversarial CLI tester. You received the results of probing a CLI tool.

Your job:
1. Identify commands that crashed, returned unexpected errors, or timed out.
2. Flag inconsistencies: help says a flag exists but it doesn't work, or vice versa.
3. Flag missing help text, unclear descriptions, or misleading option names.
4. Flag any security concerns (e.g., commands that modify state without confirmation).
5. Rate overall CLI quality from 1-10.

Be concise. Return JSON with this structure:
{
  "score": <1-10>,
  "issues": [
    {"severity": "error|warning|info", "command": "<cmd>", "message": "<what's wrong>"}
  ],
  "suggestions": ["<improvement suggestion>"],
  "converged": <true if score >= 9 and no errors>
}
"""


def analyze_results(
    tree: HelpTree,
    results: list[CommandResult],
) -> dict:
    """Ask Claude to analyze the probe results adversarially."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return _fallback_analysis(results)

    try:
        import anthropic
    except ImportError:
        print("WARN: anthropic SDK not installed, using fallback analysis")
        return _fallback_analysis(results)

    client = anthropic.Anthropic(api_key=api_key)

    # Build context for Claude
    context = _build_context(tree, results)

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=[
            {
                "role": "user",
                "content": f"{ANALYSIS_PROMPT}\n\n---\n\n{context}",
            }
        ],
    )

    # Parse JSON from response
    response_text = message.content[0].text
    try:
        # Try to extract JSON from response (Claude sometimes wraps in markdown)
        json_match = response_text
        if "```json" in response_text:
            json_match = response_text.split("```json")[1].split("```")[0]
        elif "```" in response_text:
            json_match = response_text.split("```")[1].split("```")[0]
        return json.loads(json_match.strip())
    except (json.JSONDecodeError, IndexError):
        return {
            "score": 0,
            "issues": [{"severity": "error", "command": "N/A", "message": "Failed to parse Claude response"}],
            "suggestions": [],
            "converged": False,
            "raw_response": response_text,
        }


def _build_context(tree: HelpTree, results: list[CommandResult]) -> str:
    """Build a concise context string for Claude."""
    lines = [
        f"# CLI: {tree.binary}",
        f"## Commands discovered: {len(tree.commands)}",
        f"## Total probes run: {len(results)}",
        "",
        "## Results:",
    ]

    for r in results:
        status = "OK" if r.ok else ("TIMEOUT" if r.timed_out else f"EXIT={r.exit_code}")
        lines.append(f"  [{status}] {r.command} ({r.duration_ms}ms)")
        if not r.ok and r.stderr:
            # Truncate long stderr
            stderr_short = r.stderr[:300].replace("\n", " ")
            lines.append(f"    stderr: {stderr_short}")

    return "\n".join(lines)


def _fallback_analysis(results: list[CommandResult]) -> dict:
    """Simple local analysis when Claude API is not available."""
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
        "suggestions": ["Install anthropic SDK for deeper analysis: pip install anthropic"],
        "converged": score >= 9 and not any(i["severity"] == "error" for i in issues),
    }
