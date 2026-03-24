# Improvements

- [x] [functional] Fix NameError in runner.py: `parts[0]` is undefined in FileNotFoundError handler (line 98)
- [x] [functional] Add --version flag to CLI (README references it but it does not exist)
- [x] [functional] Fix option parser regex to handle flags without short alias (e.g. --timeout TIMEOUT)
- [x] [functional] Fix option parser to detect value-taking flags with argparse-style `--flag VALUE` (no angle/square brackets)
- [x] [performance] Cache parsed help output to avoid re-running --help for already-seen subcommands
- [x] [functional] [needs-package] Add colorized terminal output with rich
