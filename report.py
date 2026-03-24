"""Generate and display probe reports."""

from __future__ import annotations

import json
from parser import HelpTree
from runner import CommandResult


def generate_report(
    tree: HelpTree,
    results: list[CommandResult],
    analysis: dict,
) -> dict:
    """Build a structured report dict."""
    ok = [r for r in results if r.ok]
    failed = [r for r in results if not r.ok and not r.timed_out]
    timed_out = [r for r in results if r.timed_out]

    return {
        "target": tree.binary,
        "summary": {
            "commands_discovered": len(tree.commands),
            "total_probes": len(results),
            "passed": len(ok),
            "failed": len(failed),
            "timed_out": len(timed_out),
            "success_rate": f"{len(ok) / len(results) * 100:.1f}%" if results else "N/A",
        },
        "analysis": analysis,
        "failures": [
            {
                "command": r.command,
                "exit_code": r.exit_code,
                "stderr": r.stderr[:500],
            }
            for r in failed
        ],
        "timeouts": [
            {"command": r.command, "duration_ms": r.duration_ms}
            for r in timed_out
        ],
    }


def print_report(report: dict) -> None:
    """Pretty-print a report to the terminal."""
    s = report["summary"]
    a = report.get("analysis", {})

    print()
    print("=" * 60)
    print(f"  CLI PROBE REPORT: {report['target']}")
    print("=" * 60)
    print(f"  Commands discovered : {s['commands_discovered']}")
    print(f"  Total probes        : {s['total_probes']}")
    print(f"  Passed              : {s['passed']}")
    print(f"  Failed              : {s['failed']}")
    print(f"  Timed out           : {s['timed_out']}")
    print(f"  Success rate        : {s['success_rate']}")
    print()

    if a.get("score") is not None:
        print(f"  Claude score        : {a['score']}/10")
        converged = a.get("converged", False)
        print(f"  Converged           : {'YES' if converged else 'NO'}")
    print()

    issues = a.get("issues", [])
    if issues:
        print("-" * 60)
        print("  ISSUES")
        print("-" * 60)
        for issue in issues:
            sev = issue.get("severity", "?").upper()
            cmd = issue.get("command", "")
            msg = issue.get("message", "")
            print(f"  [{sev}] {cmd}")
            print(f"    {msg}")
            print()

    suggestions = a.get("suggestions", [])
    if suggestions:
        print("-" * 60)
        print("  SUGGESTIONS")
        print("-" * 60)
        for s in suggestions:
            print(f"  • {s}")
        print()

    if report["failures"]:
        print("-" * 60)
        print("  FAILURES")
        print("-" * 60)
        for f in report["failures"]:
            print(f"  [{f['exit_code']}] {f['command']}")
            if f["stderr"]:
                print(f"    {f['stderr'][:200]}")
            print()

    print("=" * 60)
