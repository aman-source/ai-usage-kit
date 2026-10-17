import io
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from ai_usage_kit import cli
from ai_usage_kit.cli import main
from ai_usage_kit.navigate import build, files, query, snapshot, store
from tests.navigate.conftest import FIXTURES, init_repo


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The sharedlib fixture as its own git repo; eai state in a temp folder; cwd inside the repo."""
    root = tmp_path / "sharedlib"
    shutil.copytree(FIXTURES / "repos" / "sharedlib", root)
    init_repo(root)
    monkeypatch.setenv("EAI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.chdir(root)
    return root


def _ask(tool: str, args: dict[str, Any], cwd: Path, wait: float = 60.0) -> str:
    return query.answer(tool, args, cwd, os.environ, Path.home(), wait_seconds=wait)


def _built(root: Path) -> None:
    assert build.update(root, os.environ, Path.home()) == "built"


def _state(root: Path) -> Path:
    gdir = files.git_dir(root)
    assert gdir is not None
    return store.graph_dir(os.environ, Path.home(), gdir, root)


def test_first_query_builds_in_background_and_answers(repo: Path) -> None:
    out = _ask("where", {"symbol": "sharedlib.core.price"}, repo)
    assert "src/sharedlib/core.py:12-13  function  sharedlib.core.price" in out


def test_query_from_a_subfolder(repo: Path) -> None:
    _built(repo)
    out = _ask("where", {"symbol": "price"}, repo / "src" / "sharedlib")
    assert "src/sharedlib/core.py:12-13" in out


def test_who_uses_impact_outline(repo: Path) -> None:
    _built(repo)
    out = _ask("who-uses", {"symbol": "sharedlib.core.price", "kinds": ["call"], "page": 1, "depth": 2}, repo)
    assert "tests/test_core.py:11" in out
    assert "affected:" in _ask("impact", {"symbol": "sharedlib.core.price"}, repo)
    assert "12-13  function  price" in _ask("outline", {"path": "src/sharedlib/core.py"}, repo)


def test_impact_of_a_diff(repo: Path) -> None:
    _built(repo)
    core = repo / "src" / "sharedlib" / "core.py"
    core.write_text(core.read_text().replace("* (1 + tax), 2)", "* (1 + tax) + 1, 2)"), encoding="utf-8")
    diff = subprocess.run(["git", "-C", str(repo), "diff"], capture_output=True, text=True, check=True).stdout
    out = _ask("impact", {"diff": diff}, repo)
    assert "sharedlib.core.price" in out


def test_still_building_answer(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build, "start_background", lambda root: True)
    assert _ask("where", {"symbol": "price"}, repo, wait=0.2) == query.BUILDING


def test_outside_a_git_repo(tmp_path: Path, repo: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert _ask("where", {"symbol": "price"}, plain) == query.NOT_GIT


def test_stale_answer_starts_one_rebuild(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _built(repo)
    started: list[Path] = []

    def record(root: Path) -> bool:
        started.append(root)
        return True

    monkeypatch.setattr(build, "start_background", record)
    core = repo / "src" / "sharedlib" / "core.py"
    core.write_text(core.read_text() + "\n\ndef extra():\n    return 1\n", encoding="utf-8")
    later = time.time() + 5
    os.utime(core, (later, later))
    first = _ask("where", {"symbol": "price"}, repo)
    assert "stale: true" in first and "src/sharedlib/core.py" in first
    _ask("where", {"symbol": "price"}, repo)
    assert len(started) == 1


def test_graph_file_is_released_after_each_query(repo: Path) -> None:
    _built(repo)
    current = build.current_db(_state(repo))
    assert current is not None
    for tool, args in (("where", {"symbol": "price"}), ("who-uses", {"symbol": "price"})):
        assert "core.py" in _ask(tool, args, repo)
    renamed = current.with_name("moved.db")
    current.rename(renamed)  # fails on Windows if a query left the file open
    renamed.rename(current)


def test_corrupt_graph_is_rebuilt(repo: Path) -> None:
    _built(repo)
    current = build.current_db(_state(repo))
    assert current is not None
    current.write_bytes(b"junk")
    out = _ask("where", {"symbol": "sharedlib.core.price"}, repo)
    assert "src/sharedlib/core.py:12-13" in out


def test_unexpected_error_fails_open(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _built(repo)

    def boom(*_: object) -> None:
        raise RuntimeError("x")

    monkeypatch.setattr(snapshot.Graph, "index", boom)
    assert _ask("where", {"symbol": "price"}, repo) == "navigate failed (RuntimeError); use Grep/Read"


def test_tool_error_is_an_answer(repo: Path) -> None:
    _built(repo)
    assert _ask("impact", {"diff": "not a diff"}, repo).startswith("error: could not parse the diff")


# ---------------------------------------------------------------- CLI


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_cli_where(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _built(repo)
    code, out, _ = _run(["navigate", "where", "price|parse_order"], capsys)
    assert code == 0 and "== price" in out and "== parse_order" in out


def test_cli_who_uses_options(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _built(repo)
    code, out, _ = _run(
        ["navigate", "who-uses", "price", "--kind", "call", "--depth", "2", "--page", "1"], capsys
    )
    assert code == 0 and "tests/test_core.py:11" in out


@pytest.mark.parametrize(
    "argv",
    [
        ["navigate", "where"],
        ["navigate", "who-uses", "price", "--page", "x"],
        ["navigate", "who-uses", "price", "--page", "0"],
        ["navigate", "who-uses", "price", "--depth", "4"],
        ["navigate", "who-uses", "price", "--kind", "calls"],
        ["navigate", "impact"],
        ["navigate", "impact", "price", "--diff", "-"],
        ["navigate", "impact", "--diff", "missing-file.diff"],
    ],
)
def test_cli_usage_errors(repo: Path, capsys: pytest.CaptureFixture[str], argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "Traceback" not in err and "usage:" in err


def test_cli_impact_diff_from_stdin_and_file(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _built(repo)
    core = repo / "src" / "sharedlib" / "core.py"
    core.write_text(core.read_text().replace("* (1 + tax), 2)", "* (1 + tax) + 1, 2)"), encoding="utf-8")
    diff = subprocess.run(["git", "-C", str(repo), "diff"], capture_output=True, text=True, check=True).stdout
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(diff.encode("utf-8")), encoding="utf-8"))
    code, out, _ = _run(["navigate", "impact", "--diff", "-"], capsys)
    assert code == 0 and "sharedlib.core.price" in out
    patch = repo.parent / "change.diff"
    patch.write_text(diff, encoding="utf-8")
    code, out, _ = _run(["navigate", "impact", "--diff", str(patch)], capsys)
    assert code == 0 and "sharedlib.core.price" in out


def test_cli_non_ascii_on_cp1252_console(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (repo / "src" / "sharedlib" / "naïve.py").write_text("def café():\n    return 'é'\n", encoding="utf-8")
    _built(repo)
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", stream)
    assert main(["navigate", "outline", "src/sharedlib/naïve.py"]) == 0
    stream.flush()
    assert "café".encode() in buffer.getvalue()


def test_cli_update_outside_repo(tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plain = tmp_path / "plain2"
    plain.mkdir()
    assert main(["navigate", "update", "--repo", str(plain)]) == 0
    assert "not a git repository" in capsys.readouterr().out


def test_cli_update_background_returns_at_once(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(build, "start_background", lambda root: True)
    assert main(["navigate", "update", "--background"]) == 0
    assert "started" in capsys.readouterr().out


def test_cli_module_is_wired(repo: Path) -> None:
    assert hasattr(cli, "_run_navigate")


def test_outline_path_relative_to_a_subfolder(repo: Path) -> None:
    _built(repo)
    sub = repo / "src" / "sharedlib"
    out = _ask("outline", {"path": "core.py"}, sub)
    assert "12-13  function  price" in out
    assert "12-13  function  price" in _ask("outline", {"path": "src/sharedlib/core.py"}, sub)


def test_a_navigate_query_imports_little(repo: Path) -> None:
    code = (
        "import sys; from ai_usage_kit import cli; "
        "heavy = [m for m in ('ai_usage_kit.analyze.runner', 'ai_usage_kit.analyze.report', "
        "'ai_usage_kit.defaults.commands', 'ai_usage_kit.mcp_check.config') if m in sys.modules]; "
        "print(heavy)"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert done.stdout.strip() == "[]"
