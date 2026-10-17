"""Shorten Gradle build-task output to status, errors, warnings and build-scan links."""

from __future__ import annotations

import re
from collections.abc import Sequence

VERBOSE_FLAGS = frozenset({"--stacktrace", "--info", "--debug", "--full-stacktrace"})

_TASK_LINE = re.compile(r"^> Task :")
_TRY_SECTION = re.compile(r"^\* Try:|^> Run with --|^> Get more help at")
_BUILD_STATUS = re.compile(r"^BUILD (SUCCESSFUL|FAILED)")
_ACTIONABLE = re.compile(r"^\d+ actionable tasks?")
_DAEMON = re.compile(
    r"^(Starting a Gradle Daemon|Daemon will be stopped|Reusing configuration cache|Calculating task graph"
    r"|> Configure project|Deprecated Gradle features|You can use|For more on this|Configuration cache entry)"
)
_PROGRESS = re.compile(
    r"^\s*\d+%|^Downloading|^Configuring|^Resolving|^\[Incubating\]|^Wrote HTML report|^class \S+ could not"
    r"|^\[android-"
)
_ERROR = re.compile(
    r"(?i)(^FAILURE:|^\* What went wrong:|^\* Where:|> Could not|e: |error:|^Execution failed"
    r"|Lint found \d+ error)"
)
_WARNING = re.compile(r"^(w: |warning:|Warning:|WARNING:)")
_BUILD_SCAN = re.compile(r"gradle\.com/s/|Publishing build scan")
BUILD_TASKS = frozenset({"build", "assemble", "clean"})
BUILD_TASK_PREFIXES = ("assemble", "bundle", "install")
TASK_VALUE_FLAGS = frozenset({"-x", "--exclude-task"})


def detect_task(args: Sequence[str]) -> str:
    """The kind of the last task named, ignoring flags and `clean`."""
    tasks = [a.lower() for a in args if not a.startswith("-") and a.lower() != "clean"]
    task = tasks[-1] if tasks else ""
    if "connected" in task:
        return "connected"
    if "test" in task:
        return "test"
    if any(word in task for word in ("assemble", "build", "bundle", "install")):
        return "build"
    if any(word in task for word in ("lint", "ktlint", "detekt")):
        return "lint"
    if task == "check":
        return "test"
    if "dependencies" in task:
        return "dependencies"
    if not task:
        return "build"
    return "other"


def is_trimmable_build(args: Sequence[str]) -> bool:
    """True only when every task named is a plain build task (build, assemble*, bundle*, install*, clean).

    Tasks whose output is the point (buildEnvironment, help, custom report tasks) and verbose or
    warning-mode runs are never trimmed.
    """
    if VERBOSE_FLAGS.intersection(args) or any(arg.startswith("--warning-mode") for arg in args):
        return False
    tasks: list[str] = []
    skip_value = False
    for arg in args:
        if skip_value:
            skip_value = False
        elif arg in TASK_VALUE_FLAGS:
            skip_value = True
        elif not arg.startswith("-"):
            tasks.append(arg.rsplit(":", 1)[-1])
    return bool(tasks) and all(task in BUILD_TASKS or task.startswith(BUILD_TASK_PREFIXES) for task in tasks)


def keep_build_line(line: str) -> bool:
    if any(p.search(line) for p in (_TASK_LINE, _DAEMON, _PROGRESS, _TRY_SECTION)):
        return False
    return (
        any(p.search(line) for p in (_BUILD_STATUS, _ACTIONABLE, _ERROR, _WARNING, _BUILD_SCAN))
        or not line.strip()
    )


def filter_gradle_build(output: str) -> str:
    return "\n".join(line for line in output.splitlines() if keep_build_line(line))
