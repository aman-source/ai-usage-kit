"""Small text helpers shared by the filters."""

from __future__ import annotations

import re

_ANSI = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def truncate(text: str, max_len: int) -> str:
    """Cut to max_len characters, ending in '...'; never longer than max_len (min '...')."""
    if len(text) <= max_len:
        return text
    if max_len < 3:
        return "..."
    return text[: max_len - 3] + "..."
