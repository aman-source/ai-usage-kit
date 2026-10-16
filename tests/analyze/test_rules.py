from datetime import timedelta
from pathlib import Path

from ai_usage_kit.analyze.model import MAIN, build_session
from ai_usage_kit.analyze.rules import (
    RULES,
    Finding,
    Thresholds,
    big_instructions,
    cache_rebuild,
    instruction_files,
    large_tool_output,
    long_session,
    many_mcp,
    repeated_read,
    rule_totals,
    run_session_rules,
)
from tests.analyze.records import Record, assistant, mcp_announce, tool_result, tool_use

T = Thresholds()
PATH = "C:/work/repo/mod.py"


def _read(n: int, path: str = PATH, **extra: object) -> list[Record]:
    return [
        assistant(f"r{n}", minutes=n, content=[tool_use(f"t{n}", "Read", file_path=path, **extra)]),
        tool_result(f"t{n}", "x" * 400, minutes=n),
    ]


def test_repeated_read_fires_on_second_identical_read() -> None:
    s = build_session("p", "s", [(MAIN, _read(1) + _read(2) + _read(3))])
    findings = repeated_read(s, T)
    assert [f.evidence for f in findings] == [f"{PATH} read 2x [main]", f"{PATH} read 3x [main]"]
    assert findings[0].chars == 400
    assert findings[0].est_tokens == 100
    assert findings[0].fix == "read-once"


def test_edit_between_reads_resets() -> None:
    edit = [
        assistant("e", minutes=1.5,
                  content=[tool_use("te", "Edit", file_path=PATH, old_string="a", new_string="b")]),
    ]
    s = build_session("p", "s", [(MAIN, _read(1) + edit + _read(2))])
    assert repeated_read(s, T) == []


def test_different_ranges_and_threads_are_not_repeats() -> None:
    s = build_session(
        "p",
        "s",
        [(MAIN, _read(1) + _read(2, offset=100, limit=50)), ("agent-1", _read(3))],
    )
    assert repeated_read(s, T) == []


def test_read_without_file_path_is_skipped() -> None:
    records = [
        assistant(f"u{n}", content=[tool_use(f"u{n}", "Read", __unparsedToolInput="{bad")]) for n in (1, 2)
    ]
    assert repeated_read(build_session("p", "s", [(MAIN, records)]), T) == []


def test_large_tool_output_labels_bash_by_first_word() -> None:
    records = [
        assistant("b1", content=[tool_use("b1", "Bash", command="/usr/bin/pytest -q tests")]),
        tool_result("b1", "y" * 20_001),
        assistant("b2", content=[tool_use("b2", "Bash", command="git status")]),
        tool_result("b2", "y" * 20_000),
        assistant("m1", content=[tool_use("m1", "mcp__srv__q")]),
        tool_result("m1", "z" * 30_000),
    ]
    findings = large_tool_output(build_session("p", "s", [(MAIN, records)]), T)
    assert [f.evidence for f in findings] == [
        "Bash:pytest returned 20,001 chars [main]",
        "mcp:srv returned 30,000 chars [main]",
    ]
    assert findings[0].est_tokens == 5000
    assert findings[0].fix == "trim"


def test_rule_totals_covers_every_rule() -> None:
    findings = [
        Finding("cache-rebuild", "p", "s", None, "e", exact_tokens=50),
        Finding("cache-rebuild", "p", "s", None, "e", exact_tokens=25),
        Finding("large-tool-output", "p", "s", None, "e", chars=400),
    ]
    totals = rule_totals(findings)
    assert list(totals) == list(RULES)
    assert totals["cache-rebuild"] == (2, 75, 0)
    assert totals["large-tool-output"] == (1, 0, 100)
    assert totals["long-session"] == (0, 0, 0)


def test_cache_rebuild_after_idle_gap() -> None:
    records = [
        assistant("a1", minutes=0, usage=(5, 1, 2000, 200)),
        assistant("a2", minutes=3, usage=(5, 1, 2200, 10)),
        assistant("a3", minutes=20, usage=(5, 1, 0, 2300)),
        assistant("a4", minutes=40, usage=(5, 1, 2300, 0)),
    ]
    findings = cache_rebuild(build_session("p", "s", [(MAIN, records)]), T)
    assert [(f.exact_tokens, f.evidence) for f in findings] == [
        (2300, "idle 17 min, then 2,300 tokens written to cache"),
    ]
    assert findings[0].fix == "defaults"


def test_cache_rebuild_ignores_subagent_calls_and_custom_gap() -> None:
    records = [
        assistant("a1", minutes=0, usage=(5, 1, 2000, 0)),
        assistant("a2", minutes=8, usage=(5, 1, 0, 2000)),
    ]
    s = build_session("p", "s", [("agent-1", records)])
    assert cache_rebuild(s, T) == []
    s_main = build_session("p", "s", [(MAIN, records)])
    assert cache_rebuild(s_main, Thresholds(cache_gap=timedelta(minutes=10))) == []
    assert len(cache_rebuild(s_main, T)) == 1


def test_long_session_by_calls_or_context() -> None:
    small = [assistant(f"c{n}", minutes=n, usage=(1, 1, 1000, 0)) for n in range(3)]
    assert long_session(build_session("p", "s", [(MAIN, small)]), T) == []
    assert len(long_session(build_session("p", "s", [(MAIN, small)]), Thresholds(long_session_calls=2))) == 1
    big = small + [assistant("c9", minutes=9, usage=(10, 1, 160_000, 5))]
    findings = long_session(build_session("p", "s", [(MAIN, big)]), T)
    assert [(f.exact_tokens, f.evidence) for f in findings] == [
        (160_000, "4 API calls, peak context 160,015 tokens"),
    ]


def test_many_mcp_over_threshold() -> None:
    three = build_session(
        "p", "s", [(MAIN, [mcp_announce("mcp__a__x", "mcp__b__x", "mcp__c__x"), assistant("m")])]
    )
    assert many_mcp(three, T) == []
    four = build_session(
        "p", "s", [(MAIN, [mcp_announce("mcp__a__x", "mcp__b__x", "mcp__c__x", "mcp__d__x"), assistant("m")])]
    )
    findings = many_mcp(four, T)
    assert [f.evidence for f in findings] == [
        "4 MCP servers announced (a, b, c, d); first-call context 10 tokens",
    ]


def test_subagent_only_session_runs_all_rules() -> None:
    records = [assistant("s1", usage=(5, 1, 0, 100)), assistant("s2", minutes=30, usage=(5, 1, 0, 100))]
    s = build_session("p", "s", [("agent-1", records)])
    assert s.prefix_size == 105
    assert run_session_rules(s, T) == []


def test_instruction_files_are_unique_and_include_home() -> None:
    files = instruction_files(["C:/a", "C:/a"], Path("H"))
    expected = {Path("C:/a/CLAUDE.md"), Path("C:/a/.claude/CLAUDE.md"), Path("H/.claude/CLAUDE.md")}
    assert files == sorted(expected)


def test_big_instructions_reports_files_over_limit() -> None:
    texts = {Path("C:/a/CLAUDE.md"): "line\n" * 201, Path("H/.claude/CLAUDE.md"): "line\n" * 200}
    findings = big_instructions(["C:/a"], Path("H"), T, read=texts.get)
    assert [f.evidence for f in findings] == [
        f"{Path('C:/a/CLAUDE.md')}: 201 lines, 1,005 bytes (size on disk today)"
    ]
    assert findings[0].session_id == ""
    assert findings[0].fix == "defaults"


def test_cache_rebuild_needs_tokens_written() -> None:
    records = [
        assistant("a1", minutes=0, usage=(5, 1, 2000, 0)),
        assistant("a2", minutes=10, usage=(2000, 1, 0, 0)),
    ]
    assert cache_rebuild(build_session("p", "s", [(MAIN, records)]), T) == []


def test_read_from_line_one_repeats_default_read() -> None:
    s = build_session("p", "s", [(MAIN, _read(1) + _read(2, offset=1))])
    assert len(repeated_read(s, T)) == 1
