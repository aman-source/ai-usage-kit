from datetime import UTC, datetime

from ai_usage_kit.analyze.model import (
    MAIN,
    Usage,
    build_session,
    mcp_server,
    parse_timestamp,
    tool_group,
    total_usage,
)
from tests.analyze.records import (
    Record,
    assistant,
    compact_boundary,
    mcp_announce,
    prompt,
    tool_result,
    tool_use,
)


def test_split_records_become_one_call_with_last_usage() -> None:
    records = [
        assistant("m1", usage=(100, 1, 0, 2000), content=[tool_use("t1", "Read", file_path="a.py")]),
        assistant("m1", minutes=0.5, usage=(100, 40, 0, 2000)),
        tool_result("t1", "x" * 10),
    ]
    s = build_session("p", "s", [(MAIN, records)])
    assert len(s.calls) == 1
    assert s.calls[0].timestamp == datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    assert s.usage() == Usage(100, 40, 0, 2000)
    assert s.tool_calls[0].name == "Read"
    assert s.tool_calls[0].input == {"file_path": "a.py"}
    assert s.tool_calls[0].result_chars == 10


def test_synthetic_messages_are_not_api_calls() -> None:
    s = build_session("p", "s", [(MAIN, [assistant("m1", model="<synthetic>", usage=(9, 9, 9, 9))])])
    assert s.calls == []


def test_subagent_usage_is_kept_separate() -> None:
    s = build_session(
        "p",
        "s",
        [
            (MAIN, [assistant("m1", usage=(1, 2, 3, 4))]),
            ("agent-1", [assistant("x1", usage=(10, 20, 30, 40))]),
        ],
    )
    assert s.usage("main") == Usage(1, 2, 3, 4)
    assert s.usage("sub") == Usage(10, 20, 30, 40)
    assert s.usage() == Usage(11, 22, 33, 44)
    assert [c.thread for c in s.main_calls()] == [MAIN]


def test_prompts_and_compactions_count_main_thread_only() -> None:
    main = [
        prompt("fix it"),
        prompt([{"type": "text", "text": "and this"}]),
        prompt("reminder", isMeta=True),
        prompt("summary", isCompactSummary=True),
        prompt("   "),
        tool_result("t1", "out"),
        compact_boundary(),
    ]
    s = build_session("p", "s", [(MAIN, main), ("agent-1", [prompt("task"), compact_boundary()])])
    assert s.user_prompts == 2
    assert s.compactions == 1


def test_mcp_servers_from_announcements_and_tool_use() -> None:
    records = [
        mcp_announce("mcp__alpha__a", "mcp__alpha__b", "Read", "mcp__beta__x"),
        {"type": "attachment", "attachment": {"type": "deferred_tools_record",
                                              "entries": [{"name": "mcp__gamma__g"}],
                                              "nameOnlyAnnouncements": ["mcp__delta__d"]}},
        {"type": "attachment",
         "attachment": {"type": "mcp_instructions_delta", "addedNames": ["plugin:x:y"]}},
        assistant("m1", content=[tool_use("t1", "mcp__epsilon__e")]),
    ]
    s = build_session("p", "s", [(MAIN, records)])
    assert s.mcp_servers == {"alpha", "beta", "gamma", "delta", "epsilon"}
    assert s.tool_counts() == {"mcp:epsilon": 1}


def test_prefix_peak_start_models() -> None:
    s = build_session(
        "p",
        "s",
        [(MAIN, [
            assistant("m1", minutes=0, usage=(100, 1, 0, 2000)),
            assistant("m2", minutes=5, usage=(5, 1, 3000, 10), model="claude-opus-5-5"),
        ])],
    )
    assert s.prefix_size == 2100
    assert s.peak_context == 3015
    assert s.start == datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    assert s.models == ["claude-opus-5-5", "claude-sonnet-5"]


def test_tool_result_list_content_counts_text_only() -> None:
    records = [
        assistant("m1", content=[tool_use("t1", "WebFetch", url="u")]),
        tool_result("t1", [{"type": "text", "text": "abc"}, {"type": "image", "source": {}}]),
    ]
    assert build_session("p", "s", [(MAIN, records)]).tool_calls[0].result_chars == 3


def test_malformed_records_do_not_raise() -> None:
    records: list[Record] = [
        {"type": "assistant", "message": "oops"},
        {"type": "assistant", "message": {"model": "claude-sonnet-5", "usage": {"input_tokens": "7"},
                                          "content": "text"}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "missing"}]}},
        {"type": "attachment", "attachment": {"type": "deferred_tools_delta", "addedNames": "x"}},
        {"type": "assistant", "message": {"model": "claude-sonnet-5", "content": [{"type": "tool_use"}]}},
    ]
    s = build_session("p", "s", [(MAIN, records)])
    assert s.usage() == Usage()
    assert len(s.calls) == 2
    assert s.tool_calls == []


def test_mixed_timestamp_forms_compare_safely() -> None:
    assert parse_timestamp("2026-10-01T09:00:00Z") == datetime(2026, 10, 1, 9, tzinfo=UTC)
    assert parse_timestamp("2026-10-01T09:00:00") == datetime(2026, 10, 1, 9, tzinfo=UTC)
    assert parse_timestamp("2026-10-01T11:00:00+02:00") == datetime(2026, 10, 1, 9, tzinfo=UTC)
    assert parse_timestamp("yesterday") is None
    assert parse_timestamp(None) is None
    s = build_session("p", "s", [(MAIN, [
        {**assistant("m1"), "timestamp": "2026-10-01T09:00:00"},
        {**assistant("m2"), "timestamp": "2026-10-01T09:05:00Z"},
    ])])
    assert s.start == datetime(2026, 10, 1, 9, tzinfo=UTC)


def test_name_helpers_and_total_usage() -> None:
    assert mcp_server("mcp__srv__tool") == "srv"
    assert mcp_server("mcp__srv") is None
    assert mcp_server("Read") is None
    assert tool_group("mcp__srv__tool") == "mcp:srv"
    assert tool_group("Bash") == "Bash"
    a = build_session("p", "a", [(MAIN, [assistant("m1", usage=(1, 1, 1, 1))])])
    b = build_session("p", "b", [("agent-1", [assistant("m2", usage=(2, 2, 2, 2))])])
    assert total_usage([a, b]) == Usage(3, 3, 3, 3)
    assert total_usage([a, b], "sub") == Usage(2, 2, 2, 2)


def test_prompts_use_origin_kind_when_present() -> None:
    human = {"kind": "human"}
    main = [
        prompt("typed", origin=human),
        prompt([{"type": "image", "source": {}}], origin=human),
        prompt("<task-notification>done</task-notification>", origin={"kind": "task-notification"}),
        prompt([{"type": "text", "text": "later"}], origin={"kind": "task-notification"}),
    ]
    assert build_session("p", "s", [(MAIN, main)]).user_prompts == 2


def test_prompts_without_origin_skip_generated_records() -> None:
    main = [
        prompt("<command-name>/clear</command-name>"),
        prompt("<local-command-stdout>cleared</local-command-stdout>"),
        prompt("<local-command-caveat>Caveat</local-command-caveat>"),
        prompt("<task-notification>x</task-notification>"),
        prompt([{"type": "text", "text": "[Request interrupted by user]"}]),
        prompt("real question"),
    ]
    assert build_session("p", "s", [(MAIN, main)]).user_prompts == 2
