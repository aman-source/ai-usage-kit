"""A complete sample home dir with three session files.

Expected results (all derived by hand):
- sessions: 2 (C--work-secret-repo/sess-a with one subagent, C--work-other/sess-b); projects 2
- tokens main:  input 135, output 130, cache_read 6290, cache_write 5450
- tokens sub:   input 55,  output 15,  cache_read 550,  cache_write 500
- tokens all:   input 190, output 145, cache_read 6840, cache_write 5950
- api calls 9 (sess-a main 4, sess-a sub 2, sess-b 3); user prompts 2; compactions 1
- findings: repeated-read 1 (500 chars, est 125), large-tool-output 1 (25,000 chars, est 6,250),
  cache-rebuild 1 (exact 2,300), big-instructions 1 (other-repo/CLAUDE.md, 250 lines),
  many-mcp 1 (4 servers), long-session 0
- parse: 3 files, 1 bad line, 1 unknown record (mystery-record), versions {2.1.290}
- dates: 2026-10-01 to 2026-10-02
"""

from __future__ import annotations

from pathlib import Path

from tests.analyze.records import (
    assistant,
    compact_boundary,
    mcp_announce,
    prompt,
    tool_result,
    tool_use,
    write_jsonl,
)

SECRET_FILE = "C:/work/secret-repo/secret_module.py"
OTHER_FILE = "C:/work/other/config.py"
ARN_MODEL = "arn:aws:bedrock:us-east-1:123456789012:inference-profile/x"
DAY = 24 * 60


def build_fixture_tree(base: Path) -> tuple[Path, Path]:
    home = base / "home"
    root = home / ".claude" / "projects"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "CLAUDE.md").write_text("short\n" * 10, encoding="utf-8")
    other_cwd = base / "other-repo"
    other_cwd.mkdir()
    (other_cwd / "CLAUDE.md").write_text(
        "".join(f"secret instructions line {n}\n" for n in range(250)), encoding="utf-8"
    )

    write_jsonl(
        root / "C--work-secret-repo" / "sess-a.jsonl",
        [
            mcp_announce("mcp__secretsrv__lookup", "mcp__alpha__a", "mcp__beta__b", "mcp__gamma__g"),
            prompt("SECRET-PROMPT fix the failing test"),
            assistant("a1", minutes=0.1, usage=(100, 1, 0, 2000),
                      content=[tool_use("ta1", "Read", file_path=SECRET_FILE)]),
            assistant("a1", minutes=0.1, usage=(100, 40, 0, 2000),
                      content=[{"type": "text", "text": "reading"}]),
            tool_result("ta1", "x" * 500, minutes=0.2),
            assistant("a2", minutes=1, usage=(5, 30, 2100, 50),
                      content=[tool_use("ta2", "Read", file_path=SECRET_FILE)]),
            tool_result("ta2", "x" * 500, minutes=1.1),
            assistant("a3", minutes=2, usage=(5, 20, 2150, 60),
                      content=[tool_use("ta3", "Bash", command="pytest -q secret_tests")]),
            tool_result("ta3", "y" * 25_000, minutes=2.5),
            assistant("a4", minutes=20, usage=(5, 10, 0, 2300),
                      content=[tool_use("ta4", "mcp__secretsrv__lookup", query="SECRET-PROMPT")]),
            tool_result("ta4", "z" * 100, minutes=20.1),
            assistant("a5", minutes=21, model="<synthetic>", usage=(0, 0, 0, 0)),
            compact_boundary(minutes=22),
            prompt("summary of earlier work", minutes=22, isCompactSummary=True),
            prompt("<system-reminder>", minutes=22, isMeta=True),
        ],
        extra_lines=['{"type": "mystery-record"}', "{not json"],
    )
    write_jsonl(
        root / "C--work-secret-repo" / "sess-a" / "subagents" / "agent-1.jsonl",
        [
            prompt("look at SECRET-PROMPT module", minutes=3, isSidechain=True),
            assistant("s1", minutes=3.1, usage=(50, 10, 0, 500),
                      content=[tool_use("ts1", "Read", file_path=SECRET_FILE)]),
            tool_result("ts1", "x" * 500, minutes=3.2),
            assistant("s2", minutes=3.5, usage=(5, 5, 550, 0)),
        ],
    )
    cwd = str(other_cwd)
    write_jsonl(
        root / "C--work-other" / "sess-b.jsonl",
        [
            prompt("change the config", minutes=DAY, cwd=cwd),
            assistant("b1", minutes=DAY + 0.1, usage=(10, 10, 0, 1000), model="claude-opus-5-5", cwd=cwd,
                      content=[tool_use("tb1", "Read", file_path=OTHER_FILE)]),
            tool_result("tb1", "c" * 300, minutes=DAY + 0.2, cwd=cwd),
            assistant("b2", minutes=DAY + 1, usage=(5, 10, 1010, 20), model="claude-opus-5-5", cwd=cwd,
                      content=[tool_use("tb2", "Edit", file_path=OTHER_FILE,
                                        old_string="a", new_string="b")]),
            tool_result("tb2", "ok", minutes=DAY + 1.1, cwd=cwd),
            assistant("b3", minutes=DAY + 2, usage=(5, 10, 1030, 20), model=ARN_MODEL, cwd=cwd,
                      content=[tool_use("tb3", "Read", file_path=OTHER_FILE)]),
            tool_result("tb3", "c" * 300, minutes=DAY + 2.1, cwd=cwd),
        ],
    )
    return root, home
