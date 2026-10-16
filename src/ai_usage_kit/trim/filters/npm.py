"""Shorten npm install/ci/run output: drop script banners, notices, progress and blank lines.

npm WARN lines are kept (deduplicated, at most MAX_WARN) because warnings must reach the agent.
"""

from __future__ import annotations

MAX_WARN = 20


def filter_npm(output: str) -> str:
    result: list[str] = []
    seen_warnings: set[str] = set()
    extra_warnings = 0
    for line in output.splitlines():
        stripped = line.lstrip()
        if line.startswith(">") and "@" in line:
            continue
        if stripped.startswith("npm WARN"):
            if line in seen_warnings:
                continue
            seen_warnings.add(line)
            if len(seen_warnings) > MAX_WARN:
                extra_warnings += 1
                continue
            result.append(line)
            continue
        if stripped.startswith("npm notice"):
            continue
        if "\u2e29" in line or "\u2e28" in line or ("..." in line and len(line.encode("utf-8")) < 10):
            continue
        if not line.strip():
            continue
        result.append(line)
    if extra_warnings:
        result.append(f"... ({extra_warnings} more npm WARN lines in the full output)")
    return "\n".join(result) if result else "ok"
