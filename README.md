# cli-tester

Automated CLI tester with self-healing evolution loop.

Feed it any CLI binary → it parses `--help`, runs every command and option, analyzes the results adversarially via Claude, and generates a report in `runs/`.

In **evolve** mode, it loops: probe → analyze → patch source → rebuild → re-probe, until the CLI converges to a clean state.

## Usage

```bash
# Single probe pass
python cli_probe.py run <binary> [--timeout 10] [--dry-run] [-o report.json]

# Self-improving evolution loop
python cli_probe.py evolve <binary> [--rounds 5] [--target-dir ./src]
```

## Examples

```bash
# Dry-run against git (parse --help only, don't execute)
python cli_probe.py run git --dry-run

# Full probe of a local CLI
python cli_probe.py run "npx anatoly"

# Evolve mode: probe + auto-fix loop (requires ANTHROPIC_API_KEY)
ANTHROPIC_API_KEY=sk-... python cli_probe.py evolve ./my-tool --rounds 3 --target-dir ./src
```

Reports are saved automatically to `cli-tester/runs/`.

## How it works

```
cli-tester run <binary>
  │
  ├─ 1. Parse: run <binary> --help → extract commands & options
  ├─ 2. Probe: run every subcommand --help, then every boolean flag
  ├─ 3. Analyze: send results to Claude for adversarial review
  └─ 4. Report: JSON saved to runs/, terminal output

cli-tester evolve <binary>
  │
  ├─ Round 1: probe → report → Claude generates patches → apply → rebuild
  ├─ Round 2: re-probe → compare → more patches if needed
  └─ Round N: score >= 9 + no errors → CONVERGED → stop
```

## Files

| File | Role |
|------|------|
| `cli_probe.py` | Entry point, CLI argument parsing |
| `parser.py` | Parse `--help` output into structured command tree |
| `runner.py` | Execute commands, capture stdout/stderr/exit codes |
| `analyzer.py` | Claude API adversarial analysis (with local fallback) |
| `report.py` | Generate and display reports |
| `evolve.py` | Self-improving loop: probe → patch → rebuild → repeat |
| `runs/` | Auto-generated reports (JSON) |

## Requirements

- Python 3.10+
- `anthropic` SDK (optional, for Claude analysis): `pip install anthropic`
- `ANTHROPIC_API_KEY` env var (required for `evolve` mode, optional for `run`)

## Without Claude API

The `run` command works without an API key — it falls back to a local analysis that checks exit codes and timeouts. The `evolve` command requires the API key since it needs Claude to generate patches.
