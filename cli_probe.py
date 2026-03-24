#!/usr/bin/env python3
"""cli-probe — Automated CLI tester with self-healing evolution loop.

Usage:
  cli-probe run <binary>          Parse --help, run every command, report
  cli-probe evolve <binary>       Self-improving loop until convergence

Examples:
  python cli_probe.py run git
  python cli_probe.py evolve ./my-tool --rounds 3
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from parser import parse_help
from runner import run_all_commands
from analyzer import analyze_results
from report import generate_report, print_report
from evolve import evolve_loop

# Runs are saved alongside this script
RUNS_DIR = Path(__file__).parent / "runs"


def main():
    ap = argparse.ArgumentParser(
        prog="cli-probe",
        description="Probe any CLI: parse --help, run every command, analyze results.",
    )
    sub = ap.add_subparsers(dest="command", required=True)

    # --- run ---
    run_p = sub.add_parser("run", help="Single probe pass")
    run_p.add_argument("binary", help="CLI binary to test")
    run_p.add_argument("--timeout", type=int, default=10, help="Timeout per command (seconds)")
    run_p.add_argument("--dry-run", action="store_true", help="Parse only, don't execute")
    run_p.add_argument("-o", "--output", default=None, help="Save JSON report to file")

    # --- evolve ---
    ev_p = sub.add_parser("evolve", help="Self-improving loop")
    ev_p.add_argument("binary", help="CLI binary to test")
    ev_p.add_argument("--rounds", type=int, default=5, help="Max evolution rounds")
    ev_p.add_argument("--timeout", type=int, default=10, help="Timeout per command (seconds)")
    ev_p.add_argument("--target-dir", default=None, help="Source directory to patch (defaults to binary's dir)")

    args = ap.parse_args()

    if args.command == "run":
        # 1. Parse --help
        tree = parse_help(args.binary, timeout=args.timeout)
        if not tree:
            print(f"ERROR: Could not parse --help for '{args.binary}'", file=sys.stderr)
            sys.exit(1)

        # 2. Run every discovered command
        results = run_all_commands(tree, timeout=args.timeout, dry_run=args.dry_run)

        # 3. Analyze with Claude
        analysis = analyze_results(tree, results)

        # 4. Report
        report = generate_report(tree, results, analysis)
        print_report(report)

        # Always save to runs/
        output_path = _save_run(report, args.binary, args.output)
        print(f"\nReport saved to {output_path}")

    elif args.command == "evolve":
        evolve_loop(
            binary=args.binary,
            max_rounds=args.rounds,
            timeout=args.timeout,
            target_dir=args.target_dir,
        )


def _save_run(report: dict, binary: str, custom_path: str | None = None) -> Path:
    """Save report JSON to runs/ directory (or custom path)."""
    if custom_path:
        out = Path(custom_path)
    else:
        RUNS_DIR.mkdir(exist_ok=True)
        safe_name = binary.replace("/", "_").replace(" ", "_")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = RUNS_DIR / f"{safe_name}_{timestamp}.json"

    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump(report, f, indent=2)
    return out


if __name__ == "__main__":
    main()
