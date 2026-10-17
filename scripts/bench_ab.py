"""A/B benchmark: the same real bug-fix tasks with and without eai, judged by the project's own tests.

Spends Claude usage on the signed-in account (one `claude -p` session per task, side and run). Run only when
approved.

Repo and tasks: rich (Textualize/rich, about 52,000 lines of Python), two fixes from its history. Each run
starts from the parent of the fix commit with no git history (so the fix cannot be looked up), gets the
issue as the prompt, and is judged by the regression test the real fix added plus the full test suite.

Both sides are identical except for eai:
- same model, prompt, max turns, budget cap, fresh copy of the repo, `--no-session-persistence`;
- `--setting-sources project,local`, an empty `--strict-mcp-config`: no user plugins, MCP servers, user
  hooks or user settings;
- CLAUDE_CODE_DISABLE_CLAUDE_MDS=1 and CLAUDE_CODE_DISABLE_AUTO_MEMORY=1: no user CLAUDE.md, rules or memory;
- PATH without any folder holding an `eai` executable on the baseline.
The eai side gets exactly what `eai on recommended navigate` writes into a temp home: its settings.json as
the copy's `.claude/settings.local.json`, its rule files through `--append-system-prompt-file`, and a graph
built with `eai navigate update` in a per-run EAI_STATE_DIR.
Checks per run: plugins and MCP servers empty; baseline ran no `eai` command and its state dir stayed empty.

Needs the current eai installed as a tool (`uv tool install --force .`), git, uv and network for the clone.
Usage: uv run python scripts/bench_ab.py --work DIR [--runs 3] [--model sonnet] [--tasks A,B]
       [--sides base,eai] [--parallel 6] [--max-turns 40] [--budget 3]
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import stat
import statistics
import subprocess
import sys
import tarfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

REPO_URL = "https://github.com/Textualize/rich.git"
GIT = ["git", "-c", "user.name=bench", "-c", "user.email=bench@example.invalid"]
TAIL = (
    "\n\nFix it in this repository and add a regression test. Run tests with `python -m pytest`."
    " Do not commit."
)
TASKS: dict[str, dict[str, Any]] = {
    "A": {
        "fix": "39ee57df",
        "target": "tests/test_text.py::test_soft_wrap_styled",
        "prompt": (
            "Bug: with `soft_wrap=True` and a style that has a background, the style runs past the end of "
            "the text onto the newline, so the background bleeds into the next line in some terminals.\n\n"
            "```python\n"
            'console = Console(color_system="standard", width=80, force_terminal=True)\n'
            "with console.capture() as capture:\n"
            '    console.print("soft wrap is on", style="blue on white", soft_wrap=True)\n'
            '    console.print("Next line")\n'
            "```\n\n"
            "Expected output: `'\\x1b[34;47msoft wrap is on\\x1b[0m\\nNext line\\n'` (the style is reset "
            "before the newline). Today the newline is inside the styled span." + TAIL
        ),
    },
    "B": {
        "fix": "30e5ed61",
        "target": "tests/test_panel.py::test_title_text_with_panel_background",
        "prompt": (
            "Bug: a Panel's title and subtitle lose the panel's background style.\n\n"
            "```python\n"
            'Panel("Hello, World", style="on blue", title=Text("title", style="red"),\n'
            '      subtitle=Text("subtitle", style="magenta bold"))\n'
            "```\n\n"
            "The border and body are on blue, but the title and subtitle are printed without the blue "
            "background. Expected: they keep their own style on top of the panel's style (red on blue for "
            "the title, bold magenta on blue for the subtitle)." + TAIL
        ),
    },
}
# The permanent `uv tool install` copy; under `uv run` the first eai on PATH is the project .venv copy,
# which `eai on` refuses for hook items.
CLAUDE = shutil.which("claude") or "claude"  # resolved before any PATH change; a .cmd shim on Windows
TOOL_BIN = Path(subprocess.run(["uv", "tool", "dir", "--bin"], capture_output=True, text=True).stdout.strip())
REREAD = "is unchanged since you read it earlier"
TRIMMED = "[eai trim:"
# Expected answers: baseline "no no no no", eai "yes no no yes".
CANARY = (
    "Answer only with four yes/no words separated by spaces, about the text in your context (system prompt "
    "and instructions), not about files on disk.\n"  # a newline, as in the task prompts
    '1) Does it contain the phrase "smallest complete change"? '
    "2) Does it mention Obsidian or VAULT_PATH? 3) Does it mention a skill named brainstorming? "
    "4) Does it mention `eai navigate`?"
)
FAILED = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.MULTILINE)


def run(
    cmd: list[str], cwd: Path, env: dict[str, str] | None = None, **kw: Any
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", **kw)


def remove(path: Path) -> None:
    """Delete a tree, including read-only git object files on Windows."""

    def writable(func: Any, name: str, _: Any) -> None:
        os.chmod(name, stat.S_IWRITE)
        func(name)

    if path.exists():
        shutil.rmtree(path, onerror=writable)


def venv_bin(work: Path) -> Path:
    return work / "venv" / ("Scripts" if os.name == "nt" else "bin")


def prepare(work: Path, tasks: list[str]) -> None:
    src = work / "src"
    if not src.exists():
        subprocess.run(["git", "clone", "-q", REPO_URL, str(src)], check=True)
    if not venv_bin(work).exists():
        subprocess.run(["uv", "venv", "-q", str(work / "venv"), "--python", "3.11"], check=True)
        py = venv_bin(work) / "python"
        deps = ["pytest", "pygments>=2.13", "markdown-it-py>=2.2", "attrs"]
        subprocess.run(["uv", "pip", "install", "-q", "--python", str(py), *deps], check=True)
    for task in tasks:
        fix = TASKS[task]["fix"]
        for name, rev in ((f"{task}-start", f"{fix}^"), (f"{task}-fixed", fix)):
            dest = work / "templates" / name
            if dest.exists():
                continue
            archive = subprocess.run(["git", "archive", rev], cwd=src, capture_output=True, check=True).stdout
            with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
                tar.extractall(dest, filter="data")
            run(["git", "init", "-q"], dest)
            run(["git", "add", "-A"], dest)
            run([*GIT, "commit", "-qm", "start"], dest)


def eai_config(work: Path) -> tuple[str, Path]:
    """Run the real `eai on recommended navigate` against a temp home; return its settings and rules file."""
    home = work / "eaihome"
    remove(home)
    (home / "state").mkdir(parents=True)
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "EAI_STATE_DIR": str(home / "state")}
    env["PATH"] = f"{TOOL_BIN}{os.pathsep}{env.get('PATH', '')}"
    out = run([str(TOOL_BIN / "eai"), "on", "recommended", "navigate"], work, env)
    if out.returncode:
        sys.exit(f"eai on failed:\n{out.stdout}{out.stderr}")
    rules = work / "eai-rules.md"
    texts = [p.read_text(encoding="utf-8") for p in sorted((home / ".claude" / "rules").glob("*.md"))]
    rules.write_text("\n\n".join(texts), encoding="utf-8")
    return (home / ".claude" / "settings.json").read_text(encoding="utf-8"), rules


def failures(copy: Path, py: Path) -> tuple[set[str], str]:
    out = run([str(py), "-m", "pytest", "-q", "-rfE", "-p", "no:cacheprovider", "tests"], copy, timeout=900)
    return {f.replace("\\", "/") for f in FAILED.findall(out.stdout)}, out.stdout[-600:]


def fix_tests(work: Path, task: str, copy: Path) -> None:
    """Put the real fix's test files over the copy."""
    fix = TASKS[task]["fix"]
    names = run(["git", "show", "--name-only", "--format=", fix, "--", "tests"], work / "src").stdout.split()
    for name in names:
        shutil.copy2(work / "templates" / f"{task}-fixed" / name, copy / name)


def judge(work: Path, task: str, copy: Path, py: Path) -> dict[str, Any]:
    """Full suite with the fix's tests; pass when nothing fails that does not fail on the fixed tree."""
    fix_tests(work, task, copy)
    got, tail = failures(copy, py)
    reference = json.loads((work / f"reference-{task}.json").read_text())
    new = sorted(got - set(reference))
    return {"pass": not new and TASKS[task]["target"] not in got, "new_failures": new, "pytest_tail": tail}


def reference(work: Path, task: str, py: Path) -> None:
    """Failures on the fixed tree (environment-specific) and a check that the target fails at the start."""
    path = work / f"reference-{task}.json"
    if path.exists():
        return
    fixed, _ = failures(work / "templates" / f"{task}-fixed", py)
    probe = work / "runs" / f"{task}-reference"
    remove(probe)
    shutil.copytree(work / "templates" / f"{task}-start", probe)
    fix_tests(work, task, probe)
    if TASKS[task]["target"] not in failures(probe, py)[0]:
        sys.exit(f"task {task}: target test does not fail at the start; not a valid task")
    path.write_text(json.dumps(sorted(fixed)))


def side_env(work: Path, state: Path, side: str) -> dict[str, str]:
    path = os.environ.get("PATH", "").split(os.pathsep)
    if side == "base":  # no folder that holds an eai executable
        path = [p for p in path if not any((Path(p) / n).exists() for n in ("eai", "eai.exe", "eai.EXE"))]
    else:
        path.insert(0, str(TOOL_BIN))
    return {
        **os.environ,
        "PATH": os.pathsep.join([str(venv_bin(work)), *path]),
        "EAI_STATE_DIR": str(state),
        "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    }


def one_run(
    work: Path, task: str, side: str, n: int, args: argparse.Namespace, eai: tuple[str, Path]
) -> dict[str, Any]:
    name = f"{task}-{side}-{n}"
    copy, state = work / "runs" / name, work / "state" / name
    log = work / "logs" / f"{name}.jsonl"
    if not (args.resume and "cost" in metrics(log)):
        launch(work, task, side, args, eai, copy, state, log)
    return score(work, task, side, args, copy, state, log)


def launch(
    work: Path,
    task: str,
    side: str,
    args: argparse.Namespace,
    eai: tuple[str, Path],
    copy: Path,
    state: Path,
    log: Path,
) -> None:
    for d in (copy, state):
        remove(d)
    shutil.copytree(work / "templates" / f"{task}-start", copy)
    state.mkdir(parents=True)
    env = side_env(work, state, side)
    cmd = [
        CLAUDE, "-p", "--model", args.model,  # the prompt goes on stdin: cmd.exe cuts argv at a newline
        "--setting-sources", "project,local",
        "--strict-mcp-config", "--mcp-config", str(work / "mcp-empty.json"),
        "--output-format", "stream-json", "--verbose", "--max-turns", str(args.max_turns),
        "--no-session-persistence", "--permission-mode", "acceptEdits",
        "--allowedTools", "Bash", "PowerShell", "--permission-prompts", "none",
        "--max-budget-usd", str(args.budget), "--system-prompt-snapshot", "on",
    ]  # fmt: skip
    if side == "eai":
        (copy / ".claude").mkdir()
        (copy / ".claude" / "settings.local.json").write_text(eai[0], encoding="utf-8")
        cmd += ["--append-system-prompt-file", str(eai[1])]
        built = run([str(TOOL_BIN / "eai"), "navigate", "update"], copy, env)
        if built.returncode:
            log.write_text(f"eai navigate update failed: {built.stderr[-300:]}", encoding="utf-8")
            return
    with log.open("w", encoding="utf-8") as fh:
        prompt = CANARY if args.canary else TASKS[task]["prompt"]
        subprocess.run(
            cmd, cwd=copy, env=env, input=prompt, text=True, encoding="utf-8",
            stdout=fh, stderr=subprocess.STDOUT, timeout=1800,
        )  # fmt: skip


def score(
    work: Path, task: str, side: str, args: argparse.Namespace, copy: Path, state: Path, log: Path
) -> dict[str, Any]:
    name = log.stem
    result = {"run": name, "task": task, "side": side, **metrics(log)}
    if args.canary:
        print(f"canary {name}: {result.get('answer')!r} plugins={result.get('plugins')}", flush=True)
        return result
    diff = run(["git", "diff", "--stat"], copy).stdout.strip().splitlines()
    result["diff"] = diff[-1] if diff else "no change"
    result["state_files"] = sum(1 for p in state.rglob("*") if p.is_file())
    result["isolation"] = isolation(result, side)
    result.update(judge(work, task, copy, venv_bin(work) / "python"))
    print(
        f"done {name}: pass={result['pass']} cost={result.get('cost')} turns={result.get('turns')}",
        flush=True,
    )
    return result


def metrics(log: Path) -> dict[str, Any]:
    tools: dict[str, int] = {}
    commands: list[str] = []
    m: dict[str, Any] = {"rereads_denied": 0, "trimmed": 0, "tool_result_chars": 0, "denied": 0}
    if not log.exists():
        return m
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("type") == "system" and e.get("subtype") == "init":
            m["plugins"], m["mcp_servers"] = e.get("plugins"), e.get("mcp_servers")
        m["denied"] += e.get("subtype") == "permission_denied"
        message = e.get("message")  # a string on system events such as permission_denied
        for block in (message.get("content") or []) if isinstance(message, dict) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                tools[block["name"]] = tools.get(block["name"], 0) + 1
                if block["name"] in ("Bash", "PowerShell"):
                    commands.append(str(block["input"].get("command", "")))
            elif block.get("type") == "tool_result":
                text = json.dumps(block.get("content"))
                m["tool_result_chars"] += len(text)
                m["rereads_denied"] += REREAD in text
                m["trimmed"] += TRIMMED in text
        if e.get("type") == "result":
            u = e.get("usage") or {}
            m.update(
                cost=e.get("total_cost_usd"),
                turns=e.get("num_turns"),
                seconds=round(e.get("duration_ms", 0) / 1000),
                subtype=e.get("subtype"),
                answer=e.get("result"),
                input=u.get("input_tokens", 0),
                cache_write=u.get("cache_creation_input_tokens", 0),
                cache_read=u.get("cache_read_input_tokens", 0),
                output=u.get("output_tokens", 0),
                model_usage=e.get("modelUsage"),
            )
    m["tools"], m["commands"] = tools, commands
    m["navigate_calls"] = sum("eai navigate" in c for c in commands)
    return m


def isolation(r: dict[str, Any], side: str) -> list[str]:
    problems = []
    if r.get("plugins") or r.get("mcp_servers"):
        problems.append("plugins or MCP servers loaded")
    if side == "base" and (any(re.search(r"\beai\b", c) for c in r["commands"]) or r["state_files"]):
        problems.append("eai used on the baseline")
    if "cost" not in r:
        problems.append("no result event")
    return problems


def summary(results: list[dict[str, Any]]) -> str:
    keys: tuple[str, ...] = ("cost", "turns", "seconds", "input", "cache_write", "cache_read", "output")
    keys += ("tool_result_chars", "denied")
    lines = [
        "| task | side | pass | " + " | ".join(f"median {k}" for k in keys) + " |",
        "|" + "---|" * (len(keys) + 3),
    ]
    for task in sorted({r["task"] for r in results if "task" in r}):
        for side in ("base", "eai"):
            rs = [r for r in results if r.get("task") == task and r.get("side") == side and "cost" in r]
            if not rs:
                continue
            med = [statistics.median(float(r.get(k) or 0) for r in rs) for k in keys]
            passed = f"{sum(r['pass'] for r in rs)}/{len(rs)}"
            lines.append(
                f"| {task} | {side} | {passed} | "
                + " | ".join(f"{v:,.2f}" if v < 100 else f"{v:,.0f}" for v in med)
                + " |"
            )
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--work", type=Path, required=True)
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--model", default="sonnet")
    p.add_argument("--tasks", default="A,B")
    p.add_argument("--sides", default="base,eai")
    p.add_argument("--parallel", type=int, default=6)
    p.add_argument("--max-turns", type=int, default=40)
    p.add_argument("--budget", type=float, default=3.0)
    p.add_argument(
        "--canary", action="store_true", help="one-turn context check per side instead of the tasks"
    )
    p.add_argument(
        "--resume", action="store_true", help="rescore runs whose log has a result; launch only the rest"
    )
    args = p.parse_args()
    work = args.work.resolve()
    tasks, sides = args.tasks.split(","), args.sides.split(",")
    for d in ("logs", "runs", "state"):
        (work / d).mkdir(parents=True, exist_ok=True)
    (work / "mcp-empty.json").write_text('{"mcpServers": {}}')
    prepare(work, tasks)
    py = venv_bin(work) / "python"
    for task in tasks:
        reference(work, task, py)
    eai = eai_config(work)
    jobs = [(t, s, n) for n in range(1, args.runs + 1) for t in tasks for s in sides]
    with ThreadPoolExecutor(args.parallel) as pool:
        results = list(pool.map(lambda j: one_run(work, j[0], j[1], j[2], args, eai), jobs))
    if args.canary:
        return 0
    (work / "results.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(summary(results))
    bad = [
        (r["run"], r.get("isolation") or r.get("error"))
        for r in results
        if r.get("isolation") or r.get("error")
    ]
    print("isolation problems:", bad or "none")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
