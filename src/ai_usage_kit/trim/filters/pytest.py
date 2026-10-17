"""Shorten pytest output to its summary, expected-failure outcomes, failures and warnings."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ai_usage_kit.trim.filters.text import strip_ansi, truncate

NO_TESTS = "Pytest: No tests collected"
MAX_XFAIL = 10
MAX_FAILURES = 10
MAX_WARNING_LINES = 30
MAX_COVERAGE_LINES = 60
MAX_SKIPPED = 10
_COVERAGE_START = re.compile(r"^-{3,} coverage")


@dataclass
class Counts:
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    xfailed: int = 0
    xpassed: int = 0
    duration: str | None = None


def filter_pytest(output: str) -> str:
    state = "header"
    failures: list[str] = []
    current: list[str] = []
    xfail_lines: list[str] = []
    warnings: list[str] = []
    in_warnings = False
    coverage: list[str] = []
    in_coverage = False
    skipped: list[str] = []
    summary = ""
    for line in strip_ansi(output).splitlines():
        trimmed = line.strip()
        if in_warnings:
            if not trimmed.startswith("==="):
                if not trimmed.startswith("-- Docs:"):
                    warnings.append(line.rstrip())
                continue
            in_warnings = False
        if in_coverage:
            if not trimmed.startswith("==="):
                coverage.append(line.rstrip())
                if trimmed.startswith("TOTAL"):
                    in_coverage = False
                continue
            in_coverage = False
        if _COVERAGE_START.match(trimmed):
            in_coverage = True
            coverage.append(line.rstrip())
            continue
        if trimmed.startswith("Required test coverage"):
            coverage.append(trimmed)
            continue
        if trimmed.startswith("===") and "warnings summary" in trimmed:
            in_warnings = True
            continue
        if trimmed.startswith("===") and "test session starts" in trimmed:
            state = "header"
            continue
        if trimmed.startswith("===") and "FAILURES" in trimmed:
            state = "failures"
            continue
        if trimmed.startswith("===") and "short test summary" in trimmed:
            state = "summary"
            if current:
                failures.append("\n".join(current))
                current = []
            continue
        if trimmed.startswith("===") and ("passed" in trimmed or "failed" in trimmed or "skipped" in trimmed):
            summary = trimmed
            continue
        if (
            not summary
            and not trimmed.startswith(("===", "FAILED", "ERROR"))
            and (" passed" in trimmed or " failed" in trimmed or " skipped" in trimmed)
            and " in " in trimmed
        ):
            summary = trimmed
            continue
        if state == "header":
            if trimmed.startswith("collected"):
                state = "progress"
        elif state == "failures":
            if trimmed.startswith("___"):
                if current:
                    failures.append("\n".join(current))
                    current = []
                current.append(trimmed)
            elif trimmed and not trimmed.startswith("==="):
                current.append(trimmed)
        elif state == "summary":
            if trimmed.startswith(("FAILED", "ERROR")):
                failures.append(trimmed)
            elif trimmed.startswith(("XFAIL", "XPASS")):
                xfail_lines.append(trimmed)
            elif trimmed.startswith("SKIPPED"):
                skipped.append(trimmed)
    if current:
        failures.append("\n".join(current))
    result = _build_summary(summary, failures, xfail_lines)
    if result == NO_TESTS:
        return result
    return (
        result
        + _block("Skipped", skipped, MAX_SKIPPED, "skipped lines")
        + _block("Coverage", coverage, MAX_COVERAGE_LINES, "coverage lines")
        + _warnings_block(warnings)
    )


def _block(title: str, lines: list[str], cap: int, noun: str) -> str:
    lines = [line for line in lines if line.strip()]
    if not lines:
        return ""
    block = f"\n\n{title}:\n" + "\n".join(lines[:cap])
    if len(lines) > cap:
        block += f"\n... ({len(lines) - cap} more {noun})"
    return block


def _warnings_block(warnings: list[str]) -> str:
    lines = [line for line in warnings if line.strip()]
    if not lines:
        return ""
    shown = lines[:MAX_WARNING_LINES]
    block = "\n\nWarnings:\n" + "\n".join(shown)
    if len(lines) > MAX_WARNING_LINES:
        block += f"\n... ({len(lines) - MAX_WARNING_LINES} more warning lines)"
    return block


def _build_summary(summary: str, failures: list[str], xfail_lines: list[str]) -> str:
    c = parse_summary_line(summary)
    if not (c.passed or c.failed or c.skipped or c.xfailed or c.xpassed):
        return NO_TESTS
    extras = c.skipped > 0 or c.xfailed > 0 or c.xpassed > 0 or bool(xfail_lines)
    duration = f" in {c.duration}" if c.duration else ""
    if c.failed == 0 and c.passed > 0 and not extras:
        return f"Pytest: {c.passed} passed{duration}"
    result = f"Pytest: {c.passed} passed, {c.failed} failed"
    if c.skipped:
        result += f", {c.skipped} skipped"
    if c.xfailed:
        result += f", {c.xfailed} xfailed"
    if c.xpassed:
        result += f", {c.xpassed} xpassed"
    result += duration + "\n"
    if xfail_lines:
        result += "\nExpected-failure outcomes:\n"
        result += "".join(f"  {truncate(line, 120)}\n" for line in xfail_lines[:MAX_XFAIL])
        if len(xfail_lines) > MAX_XFAIL:
            result += f"  \u2026 +{len(xfail_lines) - MAX_XFAIL} more\n"
    if not failures:
        return result.strip()
    result += "\nFailures:\n"
    shown = failures[:MAX_FAILURES]
    for i, failure in enumerate(shown):
        lines = failure.splitlines()
        first = lines[0] if lines else ""
        if first.startswith("___"):
            result += f"{i + 1}. [FAIL] {first.strip('_').strip()}\n"
        elif first.startswith("FAILED"):
            parts = first.split(" - ")
            result += f"{i + 1}. [FAIL] {parts[0].removeprefix('FAILED ')}\n"
            if len(parts) > 1:
                result += f"     {truncate(parts[1], 100)}\n"
            continue
        relevant = 0
        for line in lines[1:]:
            lower = line.lower()
            if (
                line.strip().startswith((">", "E")) or "assert" in lower or "error" in lower or ".py:" in line
            ) and relevant < 3:
                result += f"     {truncate(line, 100)}\n"
                relevant += 1
        if i < len(failures) - 1:
            result += "\n"
    if len(failures) > MAX_FAILURES:
        result += f"\n\u2026 +{len(failures) - MAX_FAILURES} more failures\n"
    return result.strip()


def parse_summary_line(summary: str) -> Counts:
    counts = Counts(duration=parse_duration(summary))
    for part in summary.split(","):
        words = part.split()
        for i in range(1, len(words)):
            if not words[i - 1].isdigit():
                continue
            n, word = int(words[i - 1]), words[i]
            if "xpassed" in word:
                counts.xpassed = n
            elif "xfailed" in word:
                counts.xfailed = n
            elif "passed" in word:
                counts.passed = n
            elif "failed" in word:
                counts.failed = n
            elif "skipped" in word:
                counts.skipped = n
    return counts


def parse_duration(summary: str) -> str | None:
    unwrapped = summary.strip().strip("=").strip()
    if " in " not in unwrapped:
        return None
    tail = unwrapped.rsplit(" in ", 1)[1].split()
    if not tail or not tail[0].endswith("s"):
        return None
    try:
        float(tail[0][:-1])
    except ValueError:
        return None
    return tail[0]
