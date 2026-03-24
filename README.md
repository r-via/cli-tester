# cli-tester

Automated CLI tester with self-healing evolution loop powered by Claude opus.

Feed it any CLI binary → it parses `--help`, runs every command and option, analyzes the results adversarially, and generates a report.

In **evolve** mode, it loops: probe → analyze → patch → commit + push, one improvement at a time, until the CLI fully converges to its README specification.

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
├── improvements.md                    # shared — one improvement added per round
├── memory.md                          # shared — cumulative error log, compacted each round
├── 20260324_160000/                   # session 1
│   ├── conversation_loop_1.md         # full opus conversation log
│   ├── conversation_loop_2.md
│   ├── probe_round_1.txt             # post-fix probe results
│   ├── probe_round_2.txt
│   └── CONVERGED                      # written by opus when done
└── 20260324_170000/                   # session 2
    └── ...

prompts/
└── system.md                          # agent system prompt (editable by opus)
```

**Each round — one improvement at a time:**

```
1. Orchestrator probes all commands → results
2. Opus receives: README + improvements.md + memory.md + probe results + system prompt
3. Opus reads run directory (previous conversations, probe results) for context
4. Opus reads memory.md to avoid repeating past mistakes
5. Phase 1 — ERRORS: fix any crashes/tracebacks (mandatory, blocks improvements)
6. Phase 2 — IMPROVEMENT: implement the single unchecked item, verify, check it off
   Then add exactly one new improvement (the most impactful next issue)
   May also improve prompts in prompts/ if beneficial
7. Phase 3 — CONVERGENCE: only when README 100% fulfilled + best practices applied
8. Opus logs any new errors to memory.md, then compacts it (remove duplicates/stale entries)
9. Opus verifies every file it wrote/edited by reading it back
10. Git commit + push
11. Orchestrator re-probes to verify → saves probe_round_N.txt
10. Next round starts as fresh subprocess (reloaded code)
```

### `improvements.md` — the convergence tracker

Generated and maintained by opus in `runs/improvements.md`. One improvement added per round:
- A checkbox (`[ ]` pending, `[x]` done)
- A type tag: `[functional]` or `[performance]`
- Optional `[needs-package]` flag — skipped unless `--yolo`

Each round: implement the unchecked item → check it off → add one new item (if any).
When no further improvement is needed, proceed to convergence.

### `memory.md` — cumulative error log

Shared across all sessions in `runs/memory.md`. Each agent:
- Reads it at the start to avoid repeating past mistakes
- Appends any new errors encountered during the round
- Compacts it at the end: removes duplicates, removes stale entries for fixed code

### `prompts/system.md` — editable agent prompt

The system prompt is loaded from `prompts/system.md`. Opus can improve it during evolution
if it identifies ways to make the agent more effective or less error-prone.

### Convergence

Opus decides when to converge. It must verify:
- Zero errors in console output
- All output files written correctly (verified by reading them back)
- README specification 100% fulfilled
- All best practices applied (error handling, input validation, edge cases)
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
| `prompts/system.md` | Agent system prompt (editable by opus) |
| `runs/improvements.md` | Improvement checklist (one per round) |
| `runs/memory.md` | Cumulative error log (compacted each round) |

## Requirements

- Python 3.10+
- `claude-agent-sdk`: `pip install claude-agent-sdk`
- Git repository (required for `evolve` mode)
- Claude Code CLI installed and authenticated

## Without Claude Agent SDK

The `run` command works without the SDK — it falls back to a local analysis that checks exit codes and timeouts. The `evolve` command requires the SDK.
