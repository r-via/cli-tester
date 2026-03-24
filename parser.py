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

    # Recursively get subcommand help
    for cmd in tree.commands:
        sub_help = _run_help(binary, cmd.name, timeout=timeout)
        if sub_help:
            cmd.options = _parse_options_section(sub_help)
            cmd.has_required_positional = _has_required_positional(sub_help)
            _, _ = _parse_sections(sub_help)  # could recurse deeper

    return tree


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
            opt = _parse_option_line(stripped)
            if opt:
                options.append(opt)

    return commands, options


def _parse_option_line(line: str) -> Option | None:
    """Parse a single option line like '  -v, --verbose   Enable verbose output'."""
    # Pattern: optional short flag, long flag, optional value placeholder, description
    # Value placeholder can be <VAL>, [VAL], or bare UPPERCASE_WORD
    m = re.match(
        r"^(-\w)?,?\s*(--[\w-]+)(?:\s+(?:[<\[]\S+[>\]]|[A-Z][A-Z0-9_]*))?\s{2,}(.+)$",
        line,
    )
    if not m:
        return None

    # Detect value-taking flags: bracketed placeholders or bare UPPERCASE word
    # immediately after the flag and before the 2+ space description gap
    has_value = bool(re.search(r"--[\w-]+\s+(?:[<\[]\S+[>\]]|[A-Z][A-Z0-9_]*)\s{2,}", line))

    return Option(
        flag=m.group(2),
        alias=m.group(1) or None,
        description=m.group(3).strip(),
        takes_value=has_value,
    )


def _has_required_positional(text: str) -> bool:
    """Detect if a subcommand's help text shows required positional arguments.

    Looks at the usage line for non-optional positional args (words that are not
    in brackets and not flags).  E.g.:
        usage: cli-tester run [-h] [--dry-run] binary   →  True (binary is required)
        usage: cli-tester evolve [-h] binary             →  True
        usage: cli-tester status [-h]                    →  False
    """
    for line in text.splitlines():
        m = re.match(r"^[Uu]sage:\s*(.+)$", line.strip())
        if m:
            usage = m.group(1)
            # Remove bracketed optional groups  [...]
            cleaned = re.sub(r"\[.*?\]", "", usage)
            # Remove the program/command name tokens (everything before the first space gap)
            # What remains: positional arguments
            tokens = cleaned.split()
            # Skip the binary/subcommand tokens at the start — they match known command names
            # Positional args are the trailing ALLCAPS or lowercase words
            for tok in tokens:
                # Skip if it looks like a binary path or subcommand name
                if "/" in tok or tok.startswith("-"):
                    continue
                # Positional args in argparse are typically lowercase or ALLCAPS single words
                if re.match(r"^[a-z_][a-z0-9_-]*$", tok) or re.match(r"^[A-Z][A-Z0-9_]*$", tok):
                    # But skip common program name patterns
                    if tok not in ("usage", ):
                        return True
            break
    return False


def _parse_options_section(text: str) -> list[Option]:
    """Extract only the options from a help text."""
    _, options = _parse_sections(text)
    return options
