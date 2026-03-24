# cli-tester

Automated CLI tester with self-healing evolution loop powered by Claude opus.

Feed it any CLI binary → it parses `--help`, runs every command and option, analyzes the results adversarially, and generates a report.

In **evolve** mode, it loops: probe → analyze → patch → rebuild → git commit, until the CLI fully converges to its README specification.

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

## How it works

### `run` — single probe pass

1. Parse `<binary> --help` → extract commands & options
2. Run every subcommand `--help`, then every boolean flag
3. Local analysis of exit codes and failures
4. Generate JSON report + terminal output → saved to `runs/`

### `evolve` — self-improving loop

Each `evolve` session creates a timestamped run directory. Each round runs as a **separate subprocess** so code changes are picked up immediately.

```
runs/
├── improvements.md                    # shared across sessions
├── 20260324_160000/                   # session 1
│   ├── conversation_loop_1.md         # full opus conversation
│   ├── conversation_loop_2.md
│   ├── probe_round_1.txt             # post-fix probe results
│   ├── probe_round_2.txt
│   └── CONVERGED                      # written by opus when done
└── 20260324_170000/                   # session 2
    └── ...
```

**Each round:**

```
1. Orchestrator probes all commands → results
2. Opus receives: README + improvements.md + probe results + previous round's probe
3. Phase 1 — ERRORS: fix any crashes/tracebacks (mandatory, blocks improvements)
4. Phase 2 — IMPROVEMENTS: work on first unchecked item, verify fix, check it off
5. Phase 3 — CONVERGENCE: only when README 100% fulfilled + best practices + no further improvements
6. Git commit
7. Orchestrator re-probes to verify → saves probe_round_N.txt
8. Next round starts as fresh subprocess (reloaded code)
```

### `improvements.md` — the convergence tracker

Generated and maintained by opus in `runs/improvements.md`. Each improvement has:
- A checkbox (`[ ]` pending, `[x]` done)
- A type tag: `[functional]` or `[performance]`
- Optional `[needs-package]` flag — skipped unless `--yolo`

When all checkboxes are checked, opus looks for **new** improvements before converging.

### Convergence

Opus decides when to converge. It must verify:
- Zero errors in console output
- README specification 100% fulfilled
- All best practices applied
- Performance optimized where reasonable
- No further meaningful improvement identified
- It writes `CONVERGED` with its justification

### `--yolo` mode

By default, improvements that require new packages are blocked. Use `--yolo` to allow them.

## Files

| File | Role |
|------|------|
| `cli_tester.py` | Entry point, CLI argument parsing |
| `parser.py` | Parse `--help` output into structured command tree |
| `runner.py` | Execute commands, capture stdout/stderr/exit codes |
| `analyzer.py` | Claude opus agent — adversarial analysis + code fixes |
| `report.py` | Generate and display reports |
| `evolve.py` | Evolution loop orchestrator (subprocess per round) |

## Requirements

- Python 3.10+
- `claude-agent-sdk`: `pip install claude-agent-sdk`
- Git repository (required for `evolve` mode)
- Claude Code CLI installed and authenticated

## Without Claude Agent SDK

The `run` command works without the SDK — it falls back to a local analysis that checks exit codes and timeouts. The `evolve` command requires the SDK.
