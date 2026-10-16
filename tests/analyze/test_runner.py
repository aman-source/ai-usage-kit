from collections import Counter
from pathlib import Path

from ai_usage_kit.analyze.model import Usage, total_usage
from ai_usage_kit.analyze.rules import Thresholds, rule_totals
from ai_usage_kit.analyze.runner import analyze
from tests.analyze.fixture_tree import build_fixture_tree


def test_fixture_tree_totals(tmp_path: Path) -> None:
    root, home = build_fixture_tree(tmp_path)
    a = analyze(root, home)
    assert [(s.project, s.session_id) for s in a.sessions] == [
        ("C--work-other", "sess-b"),
        ("C--work-secret-repo", "sess-a"),
    ]
    assert total_usage(a.sessions, "main") == Usage(135, 130, 6290, 5450)
    assert total_usage(a.sessions, "sub") == Usage(55, 15, 550, 500)
    assert sum(len(s.calls) for s in a.sessions) == 9
    assert sum(s.user_prompts for s in a.sessions) == 2
    assert sum(s.compactions for s in a.sessions) == 1
    assert rule_totals(a.findings) == {
        "repeated-read": (1, 0, 125),
        "large-tool-output": (1, 0, 6250),
        "cache-rebuild": (1, 2300, 0),
        "big-instructions": (1, 0, 0),
        "many-mcp": (1, 0, 0),
        "long-session": (0, 0, 0),
    }
    assert (a.stats.files, a.stats.bad_lines, a.stats.unknown_types) == (3, 1, Counter({"mystery-record": 1}))
    assert a.stats.versions == {"2.1.290"}


def test_project_filter_and_thresholds(tmp_path: Path) -> None:
    root, home = build_fixture_tree(tmp_path)
    a = analyze(root, home, project="other", thresholds=Thresholds(instruction_lines=500))
    assert [s.session_id for s in a.sessions] == ["sess-b"]
    assert a.findings == []


def test_session_without_api_calls_is_skipped(tmp_path: Path) -> None:
    root, home = build_fixture_tree(tmp_path)
    (root / "C--empty").mkdir()
    (root / "C--empty" / "e.jsonl").write_text('{"type": "ai-title", "aiTitle": "t"}\n', encoding="utf-8")
    assert "e" not in [s.session_id for s in analyze(root, home).sessions]
