"""Cases ported from the reference tool's text helpers (see THIRD_PARTY_NOTICES.md)."""

from ai_usage_kit.trim.filters.text import strip_ansi, truncate


def test_truncate() -> None:
    assert truncate("hello", 10) == "hello"
    assert truncate("hello world", 8) == "hello..."
    assert truncate("hello", 5) == "hello"
    assert truncate("hello", 2) == "..."
    assert truncate("abc", 3) == "abc"
    assert truncate("hello world", 3) == "..."
    assert truncate("\u00e9\u00e9\u00e9\u00e9\u00e9", 4) == "\u00e9..."


def test_strip_ansi() -> None:
    assert strip_ansi("\x1b[32m5 passed\x1b[0m in \x1b[1;31m0.5s\x1b[0m") == "5 passed in 0.5s"
