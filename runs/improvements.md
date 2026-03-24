# Improvements

- [x] [functional] parse_help should check exit code of `--help` command — nonexistent binaries return exit 127 with shell error in stderr, which gets treated as valid help text. Should return None when exit code is non-zero.
- [ ] [functional] `_has_required_positional` misidentifies program/subcommand names in usage line as positional args — the skip logic only excludes "usage" but binary name tokens like "cli-tester", "run", "evolve" also match the lowercase pattern. Should skip known command names from the tree.
- [ ] [functional] Report JSON should include individual probe results (command, exit_code, duration_ms, ok) not just failures — makes reports more useful for analysis.
- [ ] [functional] `_find_source_dir` calls `which` on multi-word binaries like "python3 cli_tester.py" which fails — should split and resolve the last token first.
- [ ] [performance] `lru_cache` on `_run_help_cached` persists across calls — in evolve mode, stale cache from round N could leak into round N+1 subprocess. Should clear cache or scope it per invocation.
- [ ] [functional] Report doesn't list discovered commands/options in the JSON output — adding a "commands" section would make reports self-contained.
- [ ] [functional] Parser doesn't handle CLIs that only support `-h` instead of `--help` — should try `-h` as fallback when `--help` fails.
- [ ] [functional] The `run` command doesn't print any detail about discovered commands before running probes — adding a brief discovery summary would improve UX.
