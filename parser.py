"""Parse --help output into a structured command tree."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache


@dataclass
class Option:
    flag: str
    alias: str | None = None
    description: str = ""
    takes_value: bool = False

    def __str__(self):
        parts = []
        if self.alias:
            parts.append(self.alias)
        parts.append(self.flag)
        return ", ".join(parts)


@dataclass
class Command:
    name: str
    description: str = ""
    options: list[Option] = field(default_factory=list)
    subcommands: list[Command] = field(default_factory=list)
    has_required_positional: bool = False


@dataclass
class HelpTree:
    binary: str
    help_text: str
    commands: list[Command] = field(default_factory=list)
    global_options: list[Option] = field(default_factory=list)


def parse_help(binary: str, timeout: int = 10) -> HelpTree | None:
    """Run <binary> --help and parse the output into a HelpTree."""
    help_text = _run_help(binary, timeout=timeout)
    if help_text is None:
        return None

    tree = HelpTree(binary=binary, help_text=help_text)
    tree.commands, tree.global_options = _parse_sections(help_text)

    # Collect known command names for positional-arg detection
    known_commands = {cmd.name for cmd in tree.commands}

    # Recursively get subcommand help
    for cmd in tree.commands:
        sub_help = _run_help(binary, cmd.name, timeout=timeout)
        if sub_help:
            cmd.options = _parse_options_section(sub_help)
            cmd.has_required_positional = _has_required_positional(
                sub_help, binary, known_commands,
            )
            _, _ = _parse_sections(sub_help)  # could recurse deeper

    return tree


def clear_help_cache() -> None:
    """Clear the help output cache so re-probes get fresh results."""
    _run_help_cached.cache_clear()


def _run_help(binary: str, *subcommands: str, timeout: int = 10) -> str | None:
    """Execute a --help command and return stdout+stderr (cached)."""
    return _run_help_cached(binary, subcommands, timeout)


@lru_cache(maxsize=256)
def _run_help_cached(binary: str, subcommands: tuple[str, ...], timeout: int) -> str | None:
    """Cached inner helper — lru_cache requires hashable args."""
    cmd = f"{binary} {' '.join(subcommands)} --help".strip()
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        # Exit code 127 = command not found, 126 = permission denied
        # Don't treat shell error messages as valid help text
        if result.returncode in (126, 127) and not result.stdout:
            return None
        # Some CLIs print help to stderr
        return result.stdout or result.stderr or None
    except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError):
        return None


def _parse_sections(text: str) -> tuple[list[Command], list[Option]]:
    """Extract commands and options from help text."""
    commands: list[Command] = []
    options: list[Option] = []

    section = None
    for line in text.splitlines():
        stripped = line.strip()

        # Detect section headers (various formats)
        if re.match(r"^(commands|subcommands|available commands|positional arguments)\s*:?\s*$", stripped, re.I):
            section = "commands"
            continue
        # Argparse-style: "{run,evolve}" — extract subcommands inline
        if re.match(r"^\{[\w,_-]+\}$", stripped):
            for name in stripped.strip("{}").split(","):
                name = name.strip()
                if name:
                    commands.append(Command(name=name, description=""))
            continue
        if re.match(r"^(options|flags|global options)\s*:?\s*$", stripped, re.I):
            section = "options"
            continue
        # Git-style section headers: "start a working area (see also: ...)"
        if re.match(r"^[a-z].*\(see also:", stripped, re.I):
            section = "commands"
            continue
        # Generic prose headers that introduce command lists
        if re.match(r"^(these are|the most|main|common)\s", stripped, re.I):
            section = "commands"
            continue
        # Blank line — don't reset section, many CLIs have gaps within sections
        if not stripped:
            continue
        if stripped.startswith("Usage:") or stripped.startswith("Examples:"):
            section = None
            continue

        if section == "commands":
            # Format: "  command   Description" (indented, 2+ space gap)
            m = re.match(r"^(\S+)\s{2,}(.+)$", stripped)
            if m:
                name = m.group(1)
                desc = m.group(2).strip()
                # Update description if command already exists (from argparse {cmd1,cmd2} line)
                existing = next((c for c in commands if c.name == name), None)
                if existing:
                    existing.description = desc
                else:
                    commands.append(Command(name=name, description=desc))

        elif section == "options":
            # Detect continuation lines: indented text that doesn't start
            # with a dash is a wrapped description from the previous option.
            # We check the *original* line (not stripped) for leading whitespace,
            # and that the content doesn't begin with '-'.
            if options and line != stripped and not stripped.startswith("-"):
                # Continuation line — append to previous option's description
                options[-1].description += " " + stripped
                continue
            opt = _parse_option_line(stripped)
            if opt:
                options.append(opt)

    # Filter out suppressed subcommands (argparse ==SUPPRESS== or _-prefixed internal commands)
    commands = [
        c for c in commands
        if c.description != "==SUPPRESS==" and not c.name.startswith("_")
    ]

    return commands, options


def _parse_option_line(line: str) -> Option | None:
    """Parse a single option line like '  -v, --verbose   Enable verbose output'.

    Supports:
    - Long flag only:          --verbose  Description
    - Short + long:            -v, --verbose  Description
    - Short only:              -v  Description
    - Multi-letter short:      -vv, --very-verbose  Description
    - Value placeholders:      --timeout <SEC>  Description
    """
    # Value placeholder pattern: <VAL>, [VAL], or bare UPPERCASE_WORD
    _val = r"(?:\s+(?:[<\[]\S+[>\]]|[A-Z][A-Z0-9_]*))"

    # Try 1: short + long flag  (e.g. "-v, --verbose", "-vv, --very-verbose")
    m = re.match(
        rf"^(-\w+),?\s*(--[\w-]+){_val}?\s{{2,}}(.+)$",
        line,
    )
    if m:
        has_value = bool(re.search(r"--[\w-]+\s+(?:[<\[]\S+[>\]]|[A-Z][A-Z0-9_]*)\s{2,}", line))
        return Option(
            flag=m.group(2),
            alias=m.group(1),
            description=m.group(3).strip(),
            takes_value=has_value,
        )

    # Try 2: long flag only  (e.g. "--verbose")
    m = re.match(
        rf"^(--[\w-]+){_val}?\s{{2,}}(.+)$",
        line,
    )
    if m:
        has_value = bool(re.search(r"--[\w-]+\s+(?:[<\[]\S+[>\]]|[A-Z][A-Z0-9_]*)\s{2,}", line))
        return Option(
            flag=m.group(1),
            alias=None,
            description=m.group(2).strip(),
            takes_value=has_value,
        )

    # Try 3: short flag only  (e.g. "-v", "-vv")
    m = re.match(
        rf"^(-\w+){_val}?\s{{2,}}(.+)$",
        line,
    )
    if m:
        short = m.group(1)
        has_value = bool(re.search(r"-\w+\s+(?:[<\[]\S+[>\]]|[A-Z][A-Z0-9_]*)\s{2,}", line))
        return Option(
            flag=short,
            alias=None,
            description=m.group(2).strip(),
            takes_value=has_value,
        )

    return None


def _has_required_positional(
    text: str,
    binary: str = "",
    known_commands: set[str] | None = None,
) -> bool:
    """Detect if a subcommand's help text shows required positional arguments.

    Looks at the usage line for non-optional positional args (words that are not
    in brackets and not flags).  E.g.:
        usage: cli-tester run [-h] [--dry-run] binary   →  True (binary is required)
        usage: cli-tester evolve [-h] binary             →  True
        usage: cli-tester status [-h]                    →  False

    Handles multi-line usage (argparse wraps long usage lines with indentation).

    *binary* and *known_commands* are used to distinguish program/subcommand
    name tokens from genuine positional arguments.
    """
    lines = text.splitlines()
    usage = ""
    collecting = False
    for line in lines:
        if not collecting:
            m = re.match(r"^[Uu]sage:\s*(.+)$", line.strip())
            if m:
                usage = m.group(1)
                collecting = True
        else:
            # Continuation lines are indented; stop at non-indented or blank
            if line and (line[0] == " " or line[0] == "\t"):
                usage += " " + line.strip()
            else:
                break

    if not usage:
        return False

    # Build set of tokens that are part of the binary invocation or known
    # subcommand names — these should not be treated as positional args.
    skip_tokens: set[str] = set()
    if known_commands:
        skip_tokens.update(known_commands)
    # The binary string may be multi-word (e.g. "python3 cli_tester.py").
    # Extract the basename-like parts so we recognise them in the usage line.
    if binary:
        for part in binary.split():
            skip_tokens.add(part)
            # Also add the stem without extension  (cli_tester.py → cli_tester)
            stem = part.rsplit(".", 1)[0] if "." in part else part
            skip_tokens.add(stem)
    # The usage line often renders the prog name differently (e.g. "cli-tester"
    # vs "cli_tester.py"), so also extract it from the usage line itself: all
    # leading tokens before the first bracket/curly/flag are program names.
    pre_bracket = re.split(r"[\[\{]", usage)[0]
    for tok in pre_bracket.split():
        if tok.startswith("-"):
            break
        skip_tokens.add(tok)

    # Remove bracketed optional groups  [...] and curly groups {...}
    cleaned = re.sub(r"\[.*?\]", "", usage)
    cleaned = re.sub(r"\{.*?\}", "", cleaned)
    tokens = cleaned.split()

    for tok in tokens:
        if tok.startswith("-") or "/" in tok:
            continue
        if tok in skip_tokens:
            continue
        # Also skip "..." (argparse ellipsis)
        if tok == "...":
            continue
        # Positional args in argparse are lowercase words or ALLCAPS
        if re.match(r"^[a-z_][a-z0-9_-]*$", tok) or re.match(r"^[A-Z][A-Z0-9_]*$", tok):
            return True
    return False


def _parse_options_section(text: str) -> list[Option]:
    """Extract only the options from a help text."""
    _, options = _parse_sections(text)
    return options
