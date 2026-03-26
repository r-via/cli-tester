# cli-tester

Automated CLI tester with self-healing evolution loop powered by Claude opus.

Feed it any CLI binary → it parses `--help`, runs every command and option, analyzes the results adversarially, and generates a report.

In **evolve** mode, it loops: probe → analyze → patch → commit + push, one improvement at a time, until the CLI fully converges to its README specification.

## Installation

```bash
pip install cli-tester
```

Or install from source:

```bash
git clone <repo-url> && cd cli-tester
pip install -e .
```

## Usage

```bash
# Single probe pass
cli-tester run <binary> [--timeout 10] [--dry-run] [-o report.json] [--format json]

# Compare two probe reports
cli-tester compare <report1.json> <report2.json> [--format json|text]

# Watch mode — re-probe on source file changes
cli-tester watch <binary> [--timeout 10] [--glob "*.py"]

# Self-improving evolution loop
cli-tester evolve <binary> [--rounds 5] [--target-dir ./src]

# Allow adding new packages during evolution
cli-tester evolve <binary> --yolo

# Parallel probing
cli-tester run <binary> --parallel 4
```

### Global flags

```bash
cli-tester <command> --no-color     # Disable colored output
cli-tester <command> --quiet        # Minimal output (errors only)
cli-tester <command> --verbose      # Show all probe details
```

## Configuration

cli-tester looks for `.cli-tester.yaml` in the current directory (or specify `--config <path>`):

```yaml
binary: "python3 my_tool.py"
timeout: 15
parallel: 4
exclude_commands:
  - dangerous-reset
  - drop-database
expected_exit_codes:
  "grep --not-found": 1
  "diff file1 file2": 1
report:
  format: junit
  output: reports/
```

When a config file is present, you can run simply:

```bash
cli-tester run          # uses binary from config
cli-tester evolve       # uses binary + settings from config
```

CLI arguments override config file values.

## Examples

```bash
# Dry-run against git (parse --help only, preview what would be tested)
cli-tester run git --dry-run

# Full probe of a local CLI with JUnit output for CI
cli-tester run "npx anatoly" --format junit -o results.xml

# Compare yesterday's probe against today's
cli-tester compare runs/report-v1.json runs/report-v2.json

# Watch mode during development
cli-tester watch "python3 my_cli.py" --glob "src/**/*.py"

# Evolve mode: self-improving loop
cli-tester evolve "python3 cli_tester.py" --rounds 10

# Parallel probing for large CLIs
cli-tester run kubectl --parallel 8 --timeout 5
```

## How it works

### `run` — single probe pass

1. Parse `<binary> --help` → extract commands & options (via parser plugins)
2. Run every subcommand `--help`, then every boolean flag (parallel if `--parallel`)
3. Local analysis of exit codes and failures
4. Generate report (JSON, JUnit XML, HTML, or Markdown) + terminal output → saved to `runs/`

#### Progress display

During probing, a progress bar shows real-time status:

```
Testing kubectl: ████████░░ 80% (42/53 commands) · 3 failed · ETA 12s
```

After completion, a summary dashboard shows pass rate, skip count, and top failures.

### `compare` — diff two probe runs

```bash
cli-tester compare runs/report-v1.json runs/report-v2.json
```

Outputs:
- New commands/options discovered
- Removed commands/options
- Changed exit codes (regressions)
- Changed pass/fail status per probe
- Overall pass rate delta

Use in CI to catch CLI regressions between versions.

### `watch` — re-probe on file changes

```bash
cli-tester watch "python3 my_cli.py" --glob "src/**/*.py"
```

Monitors source files for changes, then automatically re-probes and displays a diff against the previous run. Useful during active development to see how code changes affect CLI behavior.

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

agents/                                # agent personas for party mode
├── analyst.md
├── architect.md
├── dev.md
├── pm.md
├── qa.md
├── sm.md
├── ux-designer.md
└── quick-flow-solo-dev.md

workflows/
└── party-mode/                        # multi-agent brainstorming workflow
    ├── workflow.md
    └── steps/
        ├── step-01-agent-loading.md
        ├── step-02-discussion-orchestration.md
        └── step-03-graceful-exit.md

tests/
├── conftest.py                        # shared fixtures
├── fixtures/                          # sample --help outputs from real CLIs
│   ├── git_help.txt
│   ├── docker_help.txt
│   ├── kubectl_help.txt
│   └── cargo_help.txt
├── mock_cli.py                        # mock CLI for end-to-end tests
├── test_parser.py                     # unit tests for help parsing
├── test_runner.py                     # unit tests for command execution
├── test_report.py                     # unit + snapshot tests for reports
├── test_compare.py                    # tests for compare command
├── test_cli.py                        # end-to-end CLI tests
└── test_evolve.py                     # integration tests for evolve loop
```

**Each round — one improvement at a time:**

```
1. Orchestrator probes all commands → results (parallel if configured)
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
12. Next round starts as fresh subprocess (reloaded code)

--- after convergence ---

13. Party mode: all agents brainstorm next evolution
14. Agents produce:
    - README_proposal.md — the proposed new specification
    - party_report.md — full discussion log explaining each agent's reasoning
15. Operator reviews both files to understand the logic behind the proposal
16. Operator approves or rejects
17. If approved: new evolution loop against updated README
```

### Parser plugin architecture

cli-tester supports multiple help output formats through a parser plugin system:

| Plugin | Detects | Handles |
|--------|---------|---------|
| `argparse` | Python argparse-style `--help` | Default, built-in |
| `click` | Click-style `--help` with `Commands:` sections | Auto-detected |
| `cobra` | Go cobra-style with `Available Commands:` | Auto-detected |
| `clap` | Rust clap-style with `USAGE:` header | Auto-detected |
| `man` | man-page style with `.TH` headers | Via `--parser man` |
| `custom` | User-defined regex patterns | Via config file |

Parser selection is automatic based on help output heuristics. Override with `--parser <name>`.

Custom parsers can be registered in `.cli-tester.yaml`:

```yaml
parsers:
  my-format:
    command_pattern: "^  (\\w+)\\s+(.+)$"
    option_pattern: "^  --(\\w+)\\s+(.+)$"
```

### `improvements.md` — the convergence tracker

Generated and maintained by opus in `runs/improvements.md`. One improvement added per round:
- A checkbox (`[ ]` pending, `[x]` done)
- A type tag: `[functional]`, `[performance]`, or `[epic]`
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

### Exit code contracts

By default, exit code 0 means success. Override per-command in `.cli-tester.yaml`:

```yaml
expected_exit_codes:
  "grep pattern file": 1          # 1 = no match, valid behavior
  "diff file1 file2": [0, 1]     # 0 = same, 1 = different, both valid
  "test -f missing": 1            # expected to fail
```

Probes are marked as pass/fail against these contracts instead of assuming 0 = ok.

### Phase 4 — Party mode (post-convergence)

After full convergence, `evolve` launches a **party mode**: a multi-agent brainstorming
session where all available agents discuss the project's future together.

**Inputs:**
- Agent personas from `agents/*.md` (analyst, architect, dev, pm, qa, sm, ux-designer, quick-flow-solo-dev)
- Discussion workflow from `workflows/party-mode/workflow.md` and `workflows/party-mode/steps/`
- Current `README.md` (the specification they just converged to)
- Source code, `runs/improvements.md` history, `runs/memory.md` lessons learned
- Probe results from the converged session

```
CONVERGED
    │
    ├─ Party mode starts
    │   Loads personas from agents/*.md
    │   Follows workflow from workflows/party-mode/
    │   Each agent reviews: README, source code, probe results, improvements history
    │   They discuss: missing features, architecture, UX, testing, performance
    │
    ├─ Agents produce two files in runs/<session>/:
    │   ├─ README_proposal.md   — the proposed new README specification
    │   └─ party_report.md      — full discussion log with each agent's reasoning
    │
    ├─ Operator reviews both files
    │   party_report.md explains WHY each change was proposed
    │   README_proposal.md shows WHAT the next evolution looks like
    │   Accept → README_proposal.md replaces README.md
    │   Reject → evolution stops, both files kept for reference
    │
    └─ If accepted → new evolution loop starts against the updated README
```

Each agent brings their expertise from their persona file:
- **Mary** (analyst) — identifies gaps, researches best practices
- **Winston** (architect) — proposes technical structure and patterns
- **John** (pm) — prioritizes features by user impact
- **Quinn** (qa) — flags testability concerns and edge cases
- **Bob** (sm) — coordinates the discussion, ensures actionable outcomes
- **Sally** (ux-designer) — reviews CLI ergonomics and user experience
- **Amelia** (dev) — evaluates implementation feasibility
- **Barry** (quick-flow-solo-dev) — suggests rapid prototyping approaches

This creates a **continuous evolution cycle**:
1. `evolve` converges to the current README
2. Party mode envisions the next version
3. Operator approves the new README
4. `evolve` converges to the new README
5. Repeat

### Git convention

Every commit + push must follow a professional, human-readable convention:

```
<type>(<scope>): <short description>

<body — what changed and why>
```

**Types:**
- `fix` — bug fix
- `feat` — new feature or improvement
- `refactor` — code restructure without behavior change
- `perf` — performance improvement
- `docs` — documentation only
- `test` — test additions or fixes
- `chore` — tooling, config, dependencies

**Scope:** the file or module affected (e.g. `parser`, `runner`, `evolve`, `prompts`)

**Examples:**
```
fix(parser): handle argparse flags without short alias

The regex now matches --timeout TIMEOUT (bare uppercase value placeholder)
in addition to --flag <val> and --flag [val] formats.

feat(evolve): add party mode post-convergence brainstorming

After full convergence, all agents from agents/*.md are loaded for a
multi-agent discussion following workflows/party-mode/. They produce
a README_proposal.md for operator review.

feat(compare): add compare command for CI regression detection

cli-tester compare report1.json report2.json diffs probe results,
showing new/removed commands, exit code changes, and pass rate delta.

perf(runner): parallel probe execution with ThreadPoolExecutor

run_all_commands now accepts --parallel N to execute probes concurrently.
Default remains sequential for deterministic output.
```

The evolution agent must follow this convention for every commit it creates.

### `--yolo` mode

By default, improvements that require new packages are blocked. Use `--yolo` to allow them.

## Files

| File | Role |
|------|------|
| `pyproject.toml` | Package metadata, dependencies, entry points |
| `cli_tester.py` | Entry point, CLI argument parsing |
| `parser.py` | Parse `--help` output into structured command tree |
| `parsers/` | Parser plugins for different help formats (argparse, click, cobra, clap) |
| `runner.py` | Execute commands, capture stdout/stderr/exit codes (supports parallel) |
| `analyzer.py` | Claude opus agent — adversarial analysis + code fixes |
| `report.py` | Generate and display reports (JSON, JUnit XML, HTML, Markdown) |
| `compare.py` | Compare two probe reports, detect regressions |
| `watcher.py` | File watcher for `watch` mode |
| `config.py` | Load and merge `.cli-tester.yaml` with CLI args |
| `evolve.py` | Evolution loop orchestrator (subprocess per round) |
| `prompts/system.md` | Agent system prompt (editable by opus) |
| `runs/improvements.md` | Improvement checklist (one per round) |
| `runs/memory.md` | Cumulative error log (compacted each round) |
| `agents/*.md` | Agent personas for party mode brainstorming |
| `workflows/party-mode/` | Multi-agent discussion workflow |
| `tests/` | Unit, integration, and end-to-end tests |

## Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=. --cov-report=html

# Run specific test module
pytest tests/test_parser.py -v

# Run end-to-end tests only
pytest tests/test_cli.py -v
```

### Test structure

- **Unit tests** (`test_parser.py`, `test_runner.py`, `test_report.py`) — test individual functions with mocked I/O
- **Fixture tests** — parse real `--help` outputs from `tests/fixtures/` (git, docker, kubectl, cargo) and assert correct command trees
- **Snapshot tests** (`test_report.py`) — generate reports and compare against golden snapshots
- **Integration tests** (`test_evolve.py`) — test evolve loop mechanics with a mock agent
- **End-to-end tests** (`test_cli.py`) — run `cli-tester` against `tests/mock_cli.py` and verify full pipeline

### CI

GitHub Actions runs on every push and PR:
- `pytest` with Python 3.10, 3.11, 3.12
- Coverage report uploaded as artifact
- Coverage badge in README

## Requirements

- Python 3.10+
- `claude-agent-sdk`: `pip install claude-agent-sdk`
- Git repository (required for `evolve` mode)
- Claude Code CLI installed and authenticated

### Optional dependencies

- `rich` — colored terminal output, progress bars, tables (graceful fallback without it)
- `watchdog` — required for `watch` mode (`pip install cli-tester[watch]`)

## Without Claude Agent SDK

The `run`, `compare`, and `watch` commands work without the SDK — they use local analysis that checks exit codes, timeouts, and exit code contracts. The `evolve` command requires the SDK.
