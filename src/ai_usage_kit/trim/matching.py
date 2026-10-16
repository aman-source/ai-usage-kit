"""Decide whether a shell command qualifies for trimming, and with which filter.

Only a single, simple invocation qualifies: no pipes, redirects, command chains or substitutions.
Pytest runs that ask to see something other than results (captured output, collection, durations,
help) never qualify.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePath

from ai_usage_kit.trim.filters.gradle import filter_gradle_build, is_trimmable_build
from ai_usage_kit.trim.filters.npm import filter_npm
from ai_usage_kit.trim.filters.pytest import filter_pytest
from ai_usage_kit.trim.filters.uv import filter_uv_install

# A command qualifies only if it starts with one of these. Claude Code starts the hook for the same
# prefixes (see trim.install.IF_RULES), so the two can never disagree.
PREFIXES = (
    "pytest", "py.test", "python -m pytest", "python3 -m pytest", "py -m pytest", "uv run pytest",
    "uv sync", "uv pip install", "npm i", "npm ci", "npm run ", "./gradlew ", "gradlew ", "gradlew.bat ",
    "gradle ",
)
SHELL_SYNTAX = ("|", "&", ";", ">", "<", "`", "$(", "\n")
PYTHON = re.compile(r"python(3(\.\d+)?)?(\.exe)?|py(\.exe)?")
PYTEST_EXACT_SKIP = frozenset(
    {"--co", "--help", "--version", "--showlocals", "--pdb", "--trace", "--markers"}
)
PYTEST_PREFIX_SKIP = (
    "--capture", "--collect-only", "--durations", "--fixtures", "--setup", "--log-cli", "--cov",
    "--benchmark", "--hypothesis-show-statistics",
)
# Short options that show more than results: -s (output), -l (locals), -V (version), -h (help).
PYTEST_SHOW_LETTERS = frozenset("slVh")
PYTEST_VALUE_FLAGS = frozenset({"-k", "-m", "-p", "-W", "-c", "--rootdir", "--basetemp", "-n"})
SHORT_CLUSTER = re.compile(r"-[A-Za-z]+")
NPM_SUBCOMMANDS = frozenset({"install", "i", "ci", "run", "run-script"})
GRADLE = frozenset({"gradlew", "gradlew.bat", "gradle"})


@dataclass(frozen=True)
class Match:
    name: str
    filter: Callable[[str], str]


def match_command(command: str) -> Match | None:
    if not command.startswith(PREFIXES) or any(token in command for token in SHELL_SYNTAX):
        return None
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    if not words:
        return None
    program = PurePath(words[0]).name.lower()
    pytest_args = _pytest_args(program, words)
    if pytest_args is not None:
        return None if _pytest_shows_more(pytest_args) else Match("pytest", filter_pytest)
    if program == "uv" and (words[1:2] == ["sync"] or words[1:3] == ["pip", "install"]):
        return Match("uv", filter_uv_install)
    if program in ("npm", "npm.cmd") and words[1:2] and words[1] in NPM_SUBCOMMANDS:
        return Match("npm", filter_npm)
    if program in GRADLE:
        args = words[1:]
        if not is_trimmable_build(args):
            return None
        return Match("gradle", filter_gradle_build)
    return None


def _pytest_args(program: str, words: list[str]) -> list[str] | None:
    if program in ("pytest", "pytest.exe", "py.test"):
        return words[1:]
    if PYTHON.fullmatch(program) and words[1:3] == ["-m", "pytest"]:
        return words[3:]
    if program == "uv" and words[1:3] == ["run", "pytest"]:
        return words[3:]
    return None


def _pytest_shows_more(args: list[str]) -> bool:
    i = 0
    while i < len(args):
        arg = args[i]
        value = args[i + 1] if i + 1 < len(args) else ""
        if arg == "-o":
            if value.startswith("log_cli"):
                return True
            i += 2
            continue
        if arg.startswith("-o") and "log_cli" in arg:
            return True
        if arg in PYTEST_VALUE_FLAGS:
            i += 2
            continue
        if arg == "-r":
            if "A" in value or "P" in value:
                return True
            i += 2
            continue
        if arg in PYTEST_EXACT_SKIP or arg.startswith(PYTEST_PREFIX_SKIP):
            return True
        if SHORT_CLUSTER.fullmatch(arg) and _cluster_shows_more(arg[1:]):
            return True
        i += 1
    return False


def _cluster_shows_more(letters: str) -> bool:
    """`-vs`, `-sv`, `-rA`: everything after `r` is report characters, everything before is flags."""
    flags, _, report = letters.partition("r")
    return bool(PYTEST_SHOW_LETTERS.intersection(flags)) or "A" in report or "P" in report
