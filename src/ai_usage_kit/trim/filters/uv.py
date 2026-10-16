"""Shorten `uv sync` / `uv pip install` output: drop download noise, short-circuit when up to date."""

from __future__ import annotations

import re

from ai_usage_kit.trim.filters.text import strip_ansi

_UP_TO_DATE = re.compile(r"Audited \d+ package")
_STRIP = re.compile(r"^\s*$|^\s+Downloading |^\s+Using cached |^\s+Preparing ")
MAX_LINES = 20


def filter_uv_install(output: str) -> str:
    lines = [strip_ansi(line) for line in output.splitlines()]
    if _UP_TO_DATE.search("\n".join(lines)):
        return "ok (up to date)"
    kept = [line for line in lines if not _STRIP.search(line)]
    if len(kept) > MAX_LINES:
        dropped = len(kept) - MAX_LINES
        kept = [*kept[:MAX_LINES], f"... ({dropped} lines truncated)"]
    return "\n".join(kept)
