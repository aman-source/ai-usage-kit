from collections import Counter
from pathlib import Path

from ai_usage_kit.analyze.parse import ParseStats, parse_file
from tests.analyze.records import prompt, write_jsonl


def test_keeps_known_records_and_counts_the_rest(tmp_path: Path) -> None:
    path = write_jsonl(
        tmp_path / "s.jsonl",
        [prompt("hi"), {"type": "mystery-record"}],
        extra_lines=["{not json", "[1, 2]", ""],
    )
    records, stats = parse_file(path)
    assert [r["type"] for r in records] == ["user"]
    assert stats.files == 1
    assert stats.lines == 4
    assert stats.bad_lines == 2
    assert stats.unknown_types == Counter({"mystery-record": 1})
    assert stats.versions == {"2.1.290"}
    assert stats.skipped == 3


def test_record_without_type_is_unknown(tmp_path: Path) -> None:
    _, stats = parse_file(write_jsonl(tmp_path / "s.jsonl", [{"foo": 1}]))
    assert stats.unknown_types == Counter({"None": 1})


def test_unreadable_file_is_reported_not_raised(tmp_path: Path) -> None:
    missing = tmp_path / "missing.jsonl"
    records, stats = parse_file(missing)
    assert records == []
    assert stats.files == 0
    assert stats.unreadable == [missing]


def test_merge_adds_everything(tmp_path: Path) -> None:
    a = ParseStats(files=1, lines=2, bad_lines=1, unknown_types=Counter({"x": 1}), versions={"1"})
    b = ParseStats(files=1, lines=3, unknown_types=Counter({"x": 2}), versions={"2"}, unreadable=[tmp_path])
    a.merge(b)
    assert (a.files, a.lines, a.bad_lines) == (2, 5, 1)
    assert a.unknown_types == Counter({"x": 3})
    assert a.versions == {"1", "2"}
    assert a.unreadable == [tmp_path]
