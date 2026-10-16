import os
from pathlib import Path

from ai_usage_kit.analyze.report import LOCAL_ONLY_NOTE, render_markdown, render_terminal, table
from ai_usage_kit.analyze.runner import analyze
from tests.analyze.fixture_tree import build_fixture_tree

GOLDEN = Path(__file__).parent.parent / "fixtures" / "analyze" / "expected_report.md"


def test_table_right_aligns_numbers() -> None:
    assert table(["name", "n"], [("a", 1000), ("bb", 5)]) == [
        "| name |     n |",
        "|------|------:|",
        "| a    | 1,000 |",
        "| bb   |     5 |",
    ]


def test_terminal_summary(tmp_path: Path) -> None:
    root, home = build_fixture_tree(tmp_path)
    text = render_terminal(analyze(root, home), top=1)
    assert text.startswith("ai-usage-kit analyze: 2 sessions in 2 projects, 2026-10-01 to 2026-10-02\n")
    assert "9 API calls, 2 user prompts, 1 compactions" in text
    assert "Top 1 sessions by total tokens" in text
    assert "sess-b" not in text.split("Top 1 sessions")[1].split("Parsed")[0]
    assert "Parsed 3 files (0 unreadable)" in text
    text.encode("ascii")


def test_markdown_matches_golden(tmp_path: Path) -> None:
    root, home = build_fixture_tree(tmp_path)
    text = render_markdown(analyze(root, home)).replace(str(tmp_path), "<TMP>").replace("\\", "/")
    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(text, encoding="utf-8")
    assert text == GOLDEN.read_text(encoding="utf-8")


def test_table_pads_wide_characters_by_display_width() -> None:
    assert table(["p"], [("日本",), ("ab",)]) == [
        "| p    |",
        "|------|",
        "| 日本 |",
        "| ab   |",
    ]


def test_markdown_report_says_it_is_local_only(tmp_path: Path) -> None:
    root, home = build_fixture_tree(tmp_path)
    text = render_markdown(analyze(root, home))
    assert text.splitlines()[2] == LOCAL_ONLY_NOTE
