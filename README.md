# cli-tester

Automated CLI tester with self-healing evolution loop powered by Claude opus.

Feed it any CLI binary → it parses `--help`, runs every command and option, analyzes the results adversarially, and generates a report in `runs/`.

In **evolve** mode, it loops: probe → analyze → patch → rebuild → git commit, until the CLI converges to its README specification.

## Usage

```bash
# Single probe pass
python cli_tester.py run <binary> [--timeout 10] [--dry-run] [-o report.json]

# Self-improving evolution loop
python cli_tester.py evolve <binary> [--rounds 5] [--target-dir ./src]

# Allow adding new packages during evolution
python cli_tester.py evolve <binary> --yolo
```

## Examples

```bash
# Dry-run against git (parse --help only, don't execute)
python cli_tester.py run git --dry-run

# Full probe of a local CLI
python cli_tester.py run "npx anatoly"

# Evolve mode: self-improving loop
python cli_tester.py evolve "python3 cli_tester.py" --rounds 10
```

Reports are saved automatically to `runs/`.

## How it works

### `run` — single probe pass

1. Parse `<binary> --help` → extract commands & options
2. Run every subcommand `--help`, then every boolean flag
3. Send results to Claude opus with README as spec → adversarial analysis
4. Generate JSON report + terminal output

### `evolve` — self-improving loop

Each round targets the **first unchecked item** in `improvements.md`:

```
Round 1: probe → opus analyzes (README + improvements.md) → generates improvements.md
         → patches code → git commit "evolve: round 1 — <improvement>"

Round 2: re-probe → improvement achieved? → ✓ check it off → git commit "evolve: ✓ <item>"
         → target next unchecked item → patches → git commit

Round N: all checkboxes checked + score >= 9 → CONVERGED
```

### `improvements.md` — the convergence tracker

Generated and maintained by opus. Each improvement has:
- A checkbox (`[ ]` pending, `[x]` done)
- A type tag: `[functional]` or `[performance]`
- Optional `[needs-package]` flag — skipped unless `--yolo`

```markdown
# Improvements

- [x] [functional] Add --version flag
- [x] [performance] Cache parsed help output
- [ ] [functional] Better error messages for missing arguments
- [ ] [functional] [needs-package] Add colorized output with rich
```

### Convergence criteria

The loop stops when:
- All items in `improvements.md` are checked
- Score >= 9/10 (README conformance + probe success)
- No error-severity issues remain

### `--yolo` mode

By default, improvements that require new packages are blocked. Use `--yolo` to allow them.

## Files

| File | Role |
|------|------|
| `cli_tester.py` | Entry point, CLI argument parsing |
| `parser.py` | Parse `--help` output into structured command tree |
| `runner.py` | Execute commands, capture stdout/stderr/exit codes |
| `analyzer.py` | Claude opus adversarial analysis (with local fallback) |
| `report.py` | Generate and display reports |
| `evolve.py` | Self-improving loop with git commits per round |
| `improvements.md` | Auto-generated improvement checklist (convergence tracker) |
| `runs/` | Auto-generated reports (JSON) |

## Requirements

- Python 3.10+
- `claude-code-sdk`: `pip install claude-code-sdk`
- Git repository (required for `evolve` mode)
- Claude Code CLI installed and authenticated

## Without Claude Code SDK

The `run` command works without the SDK — it falls back to a local analysis that checks exit codes and timeouts. The `evolve` command requires the SDK.
