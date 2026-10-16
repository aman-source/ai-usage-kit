"""Checks that the rule texts keep every safety exception of the behavior they port (see
THIRD_PARTY_NOTICES.md) and meet the file constraints."""

import pytest

from ai_usage_kit.rules.texts import CONCISE, MINIMAL


@pytest.mark.parametrize("text", [CONCISE, MINIMAL])
def test_file_constraints(text: str) -> None:
    assert text.isascii()
    assert len(text.encode("utf-8")) < 3072
    assert text.endswith("\n") and not text.endswith("\n\n")
    assert "ai-usage-kit" in text.splitlines()[1]
    lowered = text.lower()
    assert "caveman" not in lowered and "ponytail" not in lowered


def test_concise_keeps_the_payload_and_the_exceptions() -> None:
    text = " ".join(CONCISE.lower().split())  # line wraps do not matter
    for phrase in (
        "lead with the answer",
        "no greetings",
        "code, commands, file paths",
        "error text",
        'never drop "not", "no", "never", "only" or "except"',
        "security",
        "destructive or irreversible",
        "order of steps",
        "asking the user a question",
        "commit messages",
        "one short line",
    ):
        assert phrase in text, phrase


def test_minimal_keeps_safety_and_the_closing_line() -> None:
    text = " ".join(MINIMAL.lower().split())  # line wraps do not matter
    for phrase in (
        "is it needed",
        "already something in this codebase",
        "standard library",
        "a dependency the project already has",
        "adding a dependency to save a few lines is not worth it",
        "callers, tests",
        "root cause",
        "carry its error handling and validation along",
        "name the smaller option in one line",
        "input validation, security measures",
        "at least one small test",
        "what you left out or did not verify",
    ):
        assert phrase in text, phrase
