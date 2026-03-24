"""Generate and display probe reports."""

from __future__ import annotations

import json
from runner import CommandResult


def print_probe_summary(results: list[CommandResult]) -> None:
    """Print a quick summary of probe results."""
    ok = sum(1 for r in results if r.ok)
    failed = sum(1 for r in results if not r.ok and not r.timed_out)
    timed_out = sum(1 for r in results if r.timed_out)
    total = len(results)
    rate = f"{ok / total * 100:.0f}%" if total else "N/A"

    print(f"\n  Probes: {ok}/{total} passed ({rate})", end="")
    if failed:
        print(f" · {failed} failed", end="")
    if timed_out:
        print(f" · {timed_out} timed out", end="")
    print()

    for r in results:
        if not r.ok:
            stderr_short = r.stderr[:150].replace("\n", " ") if r.stderr else ""
            print(f"    FAIL [{r.exit_code}] {r.command}")
            if stderr_short:
                print(f"         {stderr_short}")


def generate_report(tree, results: list[CommandResult], analysis: dict) -> dict:
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
            {"command": r.command, "exit_code": r.exit_code, "stderr": r.stderr[:500]}
            for r in failed
        ],
    }


def print_report(report: dict) -> None:
    """Pretty-print a full report to the terminal."""
    s = report["summary"]

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

    if report["failures"]:
        print()
        for f in report["failures"]:
            print(f"  FAIL [{f['exit_code']}] {f['command']}")
            if f["stderr"]:
                print(f"       {f['stderr'][:200]}")

    print("=" * 60)
