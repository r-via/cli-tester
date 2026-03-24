"""Generate and display probe reports."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from runner import CommandResult

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    _RICH = True
except ImportError:
    _RICH = False

_console = Console() if _RICH else None


def _status_style(r: CommandResult) -> tuple[str, str]:
    """Return (label, rich style) for a result."""
    if r.ok:
        return "OK", "bold green"
    if r.timed_out:
        return "TIMEOUT", "bold yellow"
    return f"EXIT={r.exit_code}", "bold red"


def print_probe_summary(results: list[CommandResult]) -> None:
    """Print a quick summary of probe results."""
    ok = sum(1 for r in results if r.ok)
    failed = sum(1 for r in results if not r.ok and not r.timed_out)
    timed_out = sum(1 for r in results if r.timed_out)
    total = len(results)
    rate = f"{ok / total * 100:.0f}%" if total else "N/A"

    if _RICH:
        summary = Text()
        summary.append(f"  Probes: ", style="bold")
        summary.append(f"{ok}/{total} passed ({rate})", style="bold green" if ok == total else "bold yellow")
        if failed:
            summary.append(f" · {failed} failed", style="bold red")
        if timed_out:
            summary.append(f" · {timed_out} timed out", style="bold yellow")
        _console.print(summary)

        for r in results:
            if not r.ok:
                label, style = _status_style(r)
                line = Text()
                line.append(f"    FAIL ", style="red")
                line.append(f"[{r.exit_code}] ", style="dim")
                line.append(r.command, style="white")
                _console.print(line)
                if r.stderr:
                    stderr_short = r.stderr[:150].replace("\n", " ")
                    _console.print(f"         [dim]{stderr_short}[/dim]")
    else:
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
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "binary": tree.binary,
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
        "probes": [
            {
                "command": r.command,
                "exit_code": r.exit_code,
                "duration_ms": r.duration_ms,
                "ok": r.ok,
            }
            for r in results
        ],
        "failures": [
            {"command": r.command, "exit_code": r.exit_code, "stderr": r.stderr[:500]}
            for r in failed
        ],
    }


def print_report(report: dict) -> None:
    """Pretty-print a full report to the terminal."""
    s = report["summary"]

    if _RICH:
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("Key", style="bold cyan")
        table.add_column("Value")
        if report.get("timestamp"):
            table.add_row("Timestamp", report["timestamp"])
        if report.get("binary"):
            table.add_row("Binary", report["binary"])
        table.add_row("Commands discovered", str(s["commands_discovered"]))
        table.add_row("Total probes", str(s["total_probes"]))
        table.add_row("Passed", f"[green]{s['passed']}[/green]")
        table.add_row("Failed", f"[red]{s['failed']}[/red]" if s["failed"] else "0")
        table.add_row("Timed out", f"[yellow]{s['timed_out']}[/yellow]" if s["timed_out"] else "0")
        table.add_row("Success rate", s["success_rate"])

        _console.print()
        _console.print(Panel(table, title=f"[bold]CLI PROBE REPORT: {report['target']}[/bold]", border_style="cyan"))

        if report["failures"]:
            _console.print()
            for f in report["failures"]:
                line = Text()
                line.append("  FAIL ", style="red bold")
                line.append(f"[{f['exit_code']}] ", style="dim")
                line.append(f["command"], style="white")
                _console.print(line)
                if f["stderr"]:
                    _console.print(f"       [dim]{f['stderr'][:200]}[/dim]")
    else:
        print()
        print("=" * 60)
        print(f"  CLI PROBE REPORT: {report['target']}")
        print("=" * 60)
        if report.get("timestamp"):
            print(f"  Timestamp           : {report['timestamp']}")
        if report.get("binary"):
            print(f"  Binary              : {report['binary']}")
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
