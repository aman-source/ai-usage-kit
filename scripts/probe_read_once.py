"""One-off real check of the read-once hooks against Claude Code.

Uses a few requests on the signed-in account.

Builds a temp project with notes.txt and a temp settings file with the three read-once hook groups
(the shipped `"<eai>" hook read-once` command, EAI_STATE_DIR in the temp dir) plus a recorder of
successful Read events.
Runs `claude -p` there with `--settings <temp file>`. Never writes the real ~/.claude settings.
Usage: uv run python scripts/probe_read_once.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from ai_usage_kit.hook_files import read_counts
from ai_usage_kit.read_once import state
from ai_usage_kit.read_once.install import hook_groups
from ai_usage_kit.trim.install import hook_command_for

RECORDER = """import json, sys, pathlib
data = json.loads(sys.stdin.buffer.read().decode("utf-8"))
line = {"event": data.get("hook_event_name"), "tool": data.get("tool_name"),
        "input": data.get("tool_input"), "agent": data.get("agent_id")}
with open(pathlib.Path(sys.argv[1]), "a", encoding="utf-8") as f:
    f.write(json.dumps(line) + "\\n")
"""
PROMPT = (
    "Do these steps in order using only the Read and Edit tools: 1) Read notes.txt. 2) Read notes.txt again. "
    "3) If step 2 was blocked, Read notes.txt once more. "
    "4) Edit notes.txt to change the first ALPHA to BETA. "
    "5) Read notes.txt. 6) Use the Agent tool to start one general-purpose subagent that uses the Read "
    "tool on notes.txt and reports its first line. Then reply with one line per step saying whether it "
    "succeeded or was blocked."
)


def main() -> int:
    claude, eai = shutil.which("claude"), shutil.which("eai")
    if claude is None or eai is None:
        print("FAIL: claude or eai not found; run with: uv run python scripts/probe_read_once.py")
        return 1
    tmp = Path(tempfile.mkdtemp(prefix="eai-read-once-probe-"))
    project, state_dir, events = tmp / "project", tmp / "state", tmp / "events.jsonl"
    project.mkdir()
    notes = project / "notes.txt"
    notes.write_text("".join(f"line {i}: ALPHA value {i}\n" for i in range(200)), encoding="utf-8")
    (tmp / "recorder.py").write_text(RECORDER, encoding="utf-8")
    py = Path(sys.executable).as_posix()
    record = f'"{py}" "{(tmp / "recorder.py").as_posix()}" "{events.as_posix()}"'
    command = f'EAI_STATE_DIR="{state_dir.as_posix()}" {hook_command_for(eai, "read-once")}'
    groups = hook_groups(command)
    hooks: dict[str, list[dict[str, object]]] = {event: [group] for event, group in groups.items()}
    hooks["PostToolUse"].append(
        {"matcher": "Read", "hooks": [{"type": "command", "command": record, "timeout": 10}]}
    )
    (tmp / "settings.json").write_text(json.dumps({"hooks": hooks}, indent=2), encoding="utf-8")
    args = [claude, "-p", PROMPT, "--settings", str(tmp / "settings.json"), "--output-format", "text",
            "--allowedTools", "Read", "Edit", "Agent", "--max-turns", "16", "--no-session-persistence"]
    run = subprocess.run(
        args, cwd=project, capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL
    )
    reply = run.stdout
    lines = events.read_text(encoding="utf-8").splitlines() if events.exists() else []
    reads = [json.loads(line) for line in lines]
    denials = read_counts(state.counts_file(state_dir), time.time())[0]
    checks = {
        "second unchanged read was denied exactly once": denials == 1,
        "retry after the denial and read after the edit succeeded (3 main-conversation reads)":
            len([r for r in reads if not r.get("agent")]) == 3,
        "a subagent re-reading the same unchanged file was not blocked": any(r.get("agent") for r in reads),
        "Claude reported step 2 as blocked": "block" in reply.lower(),
        "the edit happened": "BETA" in notes.read_text(encoding="utf-8"),
    }
    for name, ok in checks.items():
        print(("PASS " if ok else "FAIL ") + name)
    print(f"successful reads: {len(reads)}; denials: {denials}")
    for r in reads:
        print(f"  read input: {r.get('input')} agent={r.get('agent')}")
    print("reply:\n" + reply.strip())
    print(f"temp dir (delete when done): {tmp}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
