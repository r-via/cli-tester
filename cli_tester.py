#!/usr/bin/env python3
"""cli-tester — Automated CLI tester with self-healing evolution loop.

Usage:
  cli-tester run <binary>          Parse --help, run every command, report
  cli-tester evolve <binary>       Self-improving loop until convergence

Examples:
  python cli_tester.py run git
  python cli_tester.py evolve ./my-tool --rounds 3
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from parser import parse_help
from runner import run_all_commands
from analyzer import fallback_report
from report import generate_report, print_report, print_probe_summary
from evolve import evolve_loop, run_single_round

# Runs are saved alongside this script
RUNS_DIR = Path(__file__).parent / "runs"


def _parse_round_args():
    """Parse _round arguments directly (internal command, hidden from --help)."""
    import argparse as _ap
    p = _ap.ArgumentParser(prog="cli-tester _round")
    p.add_argument("binary")
    p.add_argument("--round-num", type=int, required=True)
    p.add_argument("--timeout", type=int, default=10)
    p.add_argument("--target-dir", default=None)
    p.add_argument("--run-dir", default=None)
    p.add_argument("--yolo", action="store_true")
    args = p.parse_args(sys.argv[2:])
    args.command = "_round"
    return args


def main():
    # Handle internal _round command before argparse to avoid leaking it in --help
    if len(sys.argv) > 1 and sys.argv[1] == "_round":
        args = _parse_round_args()
    else:
        ap = argparse.ArgumentParser(
            prog="cli-tester",
            description="Probe any CLI: parse --help, run every command, analyze results.",
        )
        ap.add_argument("--version", action="version", version="cli-tester 0.1.0")
        sub = ap.add_subparsers(dest="command", required=True)

        # --- run ---
        run_p = sub.add_parser("run", help="Single probe pass")
        run_p.add_argument("binary", help="CLI binary to test")
        run_p.add_argument("--timeout", type=int, default=10, help="Timeout per command (seconds)")
        run_p.add_argument("--dry-run", action="store_true", help="Parse only, don't execute")
        run_p.add_argument("-o", "--output", default=None, help="Save JSON report to file")

        # --- evolve ---
        ev_p = sub.add_parser("evolve", help="Self-improving loop until convergence")
        ev_p.add_argument("binary", help="CLI binary to test")
        ev_p.add_argument("--rounds", type=int, default=5, help="Max evolution rounds")
        ev_p.add_argument("--timeout", type=int, default=10, help="Timeout per command (seconds)")
        ev_p.add_argument("--target-dir", default=None, help="Source directory to patch")
        ev_p.add_argument("--yolo", action="store_true", help="Allow adding new packages/binaries")

        args = ap.parse_args()

    # Validate --timeout: must be a positive integer
    if hasattr(args, "timeout") and args.timeout is not None and args.timeout <= 0:
        print(
            f"ERROR: --timeout must be a positive integer, got {args.timeout}",
            file=sys.stderr,
        )
        sys.exit(2)

    # Validate --rounds: must be a positive integer
    if hasattr(args, "rounds") and args.rounds is not None and args.rounds <= 0:
        print(
            f"ERROR: --rounds must be a positive integer, got {args.rounds}",
            file=sys.stderr,
        )
        sys.exit(2)

    if args.command == "run":
        # 1. Parse --help
        tree = parse_help(args.binary, timeout=args.timeout)
        if not tree:
            print(f"ERROR: Could not parse --help for '{args.binary}'", file=sys.stderr)
            sys.exit(1)

        # Warn if help output yielded no subcommands and no meaningful options
        non_help_options = [
            o for o in tree.global_options
            if o.flag not in ("--help", "--version", "-h")
        ]
        if not tree.commands and not non_help_options:
            print(
                "WARNING: No subcommands or options discovered (besides --help/--version). "
                "The help output format may not be recognized — probe coverage will be limited.",
                file=sys.stderr,
            )

        # 2. Run every discovered command
        results = run_all_commands(tree, timeout=args.timeout, dry_run=args.dry_run)

        # 3. Local analysis (run mode = quick report, no agent)
        analysis = fallback_report(results)

        # 4. Report
        report = generate_report(tree, results, analysis)
        print_report(report)
        print_probe_summary(results)

        # Always save to runs/ (and to custom path if -o given)
        runs_path = _save_run(report, args.binary, args.output)
        if runs_path:
            print(f"\nReport saved to {runs_path}")
        if args.output:
            try:
                custom_out = Path(args.output)
                if custom_out.exists():
                    print(f"Also saved to {args.output}")
            except OSError:
                pass  # already warned in _save_run

    elif args.command == "evolve":
        evolve_loop(
            binary=args.binary,
            max_rounds=args.rounds,
            timeout=args.timeout,
            target_dir=args.target_dir,
            yolo=args.yolo,
        )

    elif args.command == "_round":
        run_single_round(
            binary=args.binary,
            round_num=args.round_num,
            timeout=args.timeout,
            target_dir=args.target_dir,
            yolo=args.yolo,
            run_dir=args.run_dir,
        )


def _save_run(report: dict, binary: str, custom_path: str | None = None) -> Path | None:
    """Save report JSON to runs/ directory, and also to custom path if given.

    Returns the path to the saved report, or None if saving failed.
    I/O errors are caught and reported to stderr instead of crashing.
    """
    safe_name = binary.replace("/", "_").replace(" ", "_")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    date_dir = RUNS_DIR / timestamp
    reports_dir = date_dir / "reports"
    runs_out = reports_dir / f"{safe_name}.json"

    # Save to runs/<timestamp>/reports/
    try:
        reports_dir.mkdir(parents=True, exist_ok=True)
        with open(runs_out, "w") as f:
            json.dump(report, f, indent=2)
    except OSError as e:
        print(f"WARNING: Could not save report to {runs_out}: {e}", file=sys.stderr)
        runs_out = None

    # Additionally save to custom path if -o was specified
    if custom_path:
        custom_out = Path(custom_path)
        try:
            custom_out.parent.mkdir(parents=True, exist_ok=True)
            with open(custom_out, "w") as f:
                json.dump(report, f, indent=2)
        except OSError as e:
            print(f"WARNING: Could not save report to {custom_out}: {e}", file=sys.stderr)

    return runs_out


if __name__ == "__main__":
    main()
