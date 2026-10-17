import json
import os
import shutil
import time
from pathlib import Path

import pytest

from ai_usage_kit import cli
from ai_usage_kit.cli import main
from ai_usage_kit.defaults.commands import INSTALL_TIP, Context, init, status, turn_off, turn_on, uninstall
from ai_usage_kit.defaults.managed import Managed
from ai_usage_kit.navigate import build, githooks, install, store
from ai_usage_kit.rules.texts import NAVIGATE
from tests.navigate.conftest import FIXTURES, init_repo

EAI = "C:/tools/eai.exe"


def _ctx(
    tmp_path: Path, eai: str | None = EAI, managed: Managed | None = None, cwd: Path | None = None
) -> Context:
    environ = {"EAI_STATE_DIR": str(tmp_path / "state")}
    project = cwd or tmp_path / "proj"
    project.mkdir(parents=True, exist_ok=True)
    return Context(tmp_path / "h", environ, managed or Managed(), project, eai_path=eai)


def _settings(ctx: Context) -> dict[str, object]:
    data: dict[str, object] = json.loads(ctx.settings_path.read_text(encoding="utf-8"))
    return data


def _write(ctx: Context, data: dict[str, object]) -> None:
    ctx.settings_path.parent.mkdir(parents=True, exist_ok=True)
    ctx.settings_path.write_text(json.dumps(data), encoding="utf-8")


def _rule(ctx: Context) -> Path:
    return ctx.settings_path.parent / "rules" / "eai-navigate.md"


def _repo(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    shutil.copytree(FIXTURES / "repos" / "sharedlib", root)
    init_repo(root)
    return root


def test_allow_rules_are_exact() -> None:
    assert install.ALLOW_RULES == (
        "Bash(eai navigate where *)",
        "Bash(eai navigate who-uses *)",
        "Bash(eai navigate impact *)",
        "Bash(eai navigate outline *)",
        "PowerShell(eai navigate where *)",
        "PowerShell(eai navigate who-uses *)",
        "PowerShell(eai navigate impact *)",
        "PowerShell(eai navigate outline *)",
    )


def test_rule_text() -> None:
    assert len(NAVIGATE.encode("utf-8")) < 1200 and NAVIGATE.isascii()
    for word in (
        "eai navigate where",
        "who-uses",
        "impact",
        "outline",
        "before Grep",
        "offset/limit",
        "|",
        "--depth",
    ):
        assert word in NAVIGATE
    assert "stale or not found" in NAVIGATE


def test_needs_permanent_eai(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, eai=None)
    result = turn_on(ctx, "navigate")
    assert result.code == 1 and result.lines == [INSTALL_TIP]
    assert not ctx.settings_path.exists() and not _rule(ctx).exists()


def test_on_writes_rule_and_allow_rules_off_removes_them(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _write(ctx, {"permissions": {"allow": ["Bash(ls *)", "Bash(eai navigate where *)"]}})
    original = ctx.settings_path.read_bytes()
    result = turn_on(ctx, "navigate")
    assert result.code == 0
    text = "\n".join(result.lines)
    assert "next session" in text and "eai navigate hooks on" in text
    assert _rule(ctx).read_text(encoding="utf-8") == NAVIGATE
    allow = _settings(ctx)["permissions"]["allow"]  # type: ignore[index]
    assert allow[:2] == ["Bash(ls *)", "Bash(eai navigate where *)"]
    assert sorted(allow) == sorted(["Bash(ls *)", *install.ALLOW_RULES])
    turn_off(ctx, "navigate")
    assert not _rule(ctx).exists()
    assert _settings(ctx)["permissions"]["allow"] == ["Bash(ls *)", "Bash(eai navigate where *)"]  # type: ignore[index]
    uninstall(ctx)
    assert ctx.settings_path.read_bytes() == original


def test_created_permissions_are_pruned(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _write(ctx, {"model": "opus"})
    turn_on(ctx, "navigate")
    turn_off(ctx, "navigate")
    assert _settings(ctx) == {"model": "opus"}


def test_rule_file_conflict_changes_nothing(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _write(ctx, {})
    _rule(ctx).parent.mkdir(parents=True)
    _rule(ctx).write_text("mine", encoding="utf-8")
    result = turn_on(ctx, "navigate")
    assert result.code == 1 and _settings(ctx) == {}


def test_organization_permission_lock_is_respected(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, managed=Managed(documents=[{"allowManagedPermissionRulesOnly": True}]))
    _write(ctx, {})
    result = turn_on(ctx, "navigate")
    assert result.code == 1 and "only its own permission rules" in result.lines[0]
    assert _settings(ctx) == {} and not _rule(ctx).exists()


def test_off_removes_hooks_and_graphs(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _write(ctx, {})
    turn_on(ctx, "navigate")
    a, b = _repo(tmp_path, "a"), _repo(tmp_path, "b")
    for root in (a, b):
        githooks.install(root, EAI)
        install.remember_repo(ctx.environ, ctx.home, root)
        assert build.update(root, ctx.environ, ctx.home) == "built"
    result = turn_off(ctx, "navigate")
    assert any("hooks removed" in line for line in result.lines)
    for root in (a, b):
        assert all("off" in line for line in githooks.status(root))
    assert install.recorded_repos(ctx.environ, ctx.home) == []
    assert not store.graphs_root(ctx.environ, ctx.home).exists()


def test_uninstall_removes_navigate_too(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _write(ctx, {})
    turn_on(ctx, "navigate")
    root = _repo(tmp_path, "r")
    githooks.install(root, EAI)
    install.remember_repo(ctx.environ, ctx.home, root)
    uninstall(ctx)
    assert all("off" in line for line in githooks.status(root))
    assert not _rule(ctx).exists()


def test_init_restores_missing_allow_rules_and_rule_file(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _write(ctx, {})
    turn_on(ctx, "navigate")
    _write(ctx, {})
    _rule(ctx).unlink()
    init(ctx)
    assert set(install.ALLOW_RULES) <= set(_settings(ctx)["permissions"]["allow"])  # type: ignore[index]
    assert _rule(ctx).read_text(encoding="utf-8") == NAVIGATE
    turn_off(ctx, "navigate")
    allow = _settings(ctx).get("permissions", {}).get("allow", [])  # type: ignore[attr-defined]
    assert not set(install.ALLOW_RULES) & set(allow)


def test_status_lines(tmp_path: Path) -> None:
    root = _repo(tmp_path, "r")
    ctx = _ctx(tmp_path, cwd=root)
    _write(ctx, {})
    assert any(line.split()[0] == "navigate" and "opt-in" in line for line in status(ctx).lines)
    turn_on(ctx, "navigate")
    lines = status(ctx).lines
    assert any(line.split()[0] == "navigate" and line.split()[1] == "on:" for line in lines)
    assert any("graph: not built yet" in line for line in lines)
    build.update(root, ctx.environ, ctx.home)
    graph = next(line for line in status(ctx).lines if "graph:" in line)
    assert "files" in graph and "up to date" in graph and "hooks off" in graph
    _write(ctx, {})
    assert any("allow rules are missing" in line for line in status(ctx).lines)


def test_graph_status_creates_nothing(tmp_path: Path) -> None:
    root = _repo(tmp_path, "r")
    env = {"EAI_STATE_DIR": str(tmp_path / "state")}
    assert "not built yet" in (install.graph_status(root, env, tmp_path, time.time()) or "")
    assert not (tmp_path / "state").exists()
    plain = tmp_path / "plain"
    plain.mkdir()
    assert install.graph_status(plain, env, tmp_path, time.time()) is None


# ---------------------------------------------------------------- CLI: hooks, clean, status


@pytest.fixture
def cli_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = _repo(tmp_path, "cli")
    monkeypatch.setenv("EAI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(cli, "system_managed", lambda: Managed())
    monkeypatch.setattr(cli, "_eai_path", lambda: EAI)
    monkeypatch.chdir(root)
    return root


def test_cli_hooks_on_off_status(cli_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["navigate", "hooks", "on"]) == 0
    assert "post-merge: created" in capsys.readouterr().out
    assert install.recorded_repos(os.environ, Path.home()) == [cli_repo.resolve()]
    assert main(["navigate", "hooks", "status"]) == 0
    assert "post-merge: on" in capsys.readouterr().out
    assert main(["navigate", "hooks", "off"]) == 0
    assert install.recorded_repos(os.environ, Path.home()) == []


def test_cli_hooks_need_permanent_eai(
    cli_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "_eai_path", lambda: None)
    assert main(["navigate", "hooks", "on"]) == 1
    assert INSTALL_TIP in capsys.readouterr().err


def test_cli_hooks_outside_repo(
    cli_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.chdir(plain)
    assert main(["navigate", "hooks", "on"]) == 1
    assert "not a git repository" in capsys.readouterr().err


def test_cli_clean_and_status(cli_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    other = _repo(cli_repo.parent, "other")
    build.update(cli_repo, os.environ, Path.home())
    build.update(other, os.environ, Path.home())
    assert main(["navigate", "status"]) == 0
    assert "graph:" in capsys.readouterr().out
    assert main(["navigate", "clean"]) == 0
    capsys.readouterr()
    assert main(["navigate", "status"]) == 0
    assert "not built yet" in capsys.readouterr().out
    other_status = install.graph_status(other, os.environ, Path.home(), time.time())
    assert other_status is not None and "not built yet" not in other_status


# ---------------------------------------------------------------- review fixes


@pytest.mark.parametrize("original", [None, {"model": "x"}])
def test_siblings_turned_off_one_by_one_leave_no_empty_parent(
    tmp_path: Path, original: dict[str, object] | None
) -> None:
    ctx = _ctx(tmp_path)
    if original is not None:
        _write(ctx, original)
    init(ctx)
    turn_on(ctx, "navigate")
    turn_on(ctx, "subagent-model")
    for item in ("deny-reads", "navigate", "compact", "effort", "subagent-model"):
        turn_off(ctx, item)
    data = _settings(ctx)
    assert "permissions" not in data and "env" not in data
    uninstall(ctx)
    if original is None:
        assert not ctx.settings_path.exists()
    else:
        assert _settings(ctx) == original


def test_user_created_empty_parent_is_kept(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _write(ctx, {"permissions": {}})
    turn_on(ctx, "navigate")
    turn_off(ctx, "navigate")
    assert _settings(ctx) == {"permissions": {}}


def test_cli_hooks_refuse_a_shared_hooks_folder(
    cli_repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.navigate.conftest import git

    shared = tmp_path / "company-hooks"
    git(cli_repo, "config", "core.hooksPath", str(shared))
    assert main(["navigate", "hooks", "on"]) == 1
    err = capsys.readouterr().err
    assert "shared" in err and "navigate update --background" in err
    assert not shared.exists()
    assert install.recorded_repos(os.environ, Path.home()) == []


def test_cli_hooks_unwritable_hook_no_traceback_and_recorded(
    cli_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    real = githooks._write

    def flaky(path: Path, data: bytes, mode: int) -> None:
        if path.name == "post-checkout":
            raise PermissionError(13, "Access is denied", str(path))
        real(path, data, mode)

    monkeypatch.setattr(githooks, "_write", flaky)
    code = main(["navigate", "hooks", "on"])
    out = capsys.readouterr()
    assert "Traceback" not in out.out + out.err
    assert "post-checkout: skipped: cannot write" in out.out + out.err
    assert code == 0  # the other two hooks were installed
    assert install.recorded_repos(os.environ, Path.home()) == [cli_repo.resolve()]


def test_cli_admin_error_is_one_line(
    cli_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(*_: object) -> list[str]:
        raise PermissionError(13, "Access is denied", "x")

    monkeypatch.setattr(githooks, "status", broken)
    assert main(["navigate", "hooks", "status"]) == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err and err.startswith("error:")


def test_status_prunes_orphan_graphs(tmp_path: Path) -> None:
    root = _repo(tmp_path, "r")
    ctx = _ctx(tmp_path, cwd=root)
    _write(ctx, {})
    turn_on(ctx, "navigate")
    gone = _repo(tmp_path, "gone")
    build.update(gone, ctx.environ, ctx.home)
    gone.rename(tmp_path / "moved")
    assert len(list(store.graphs_root(ctx.environ, ctx.home).iterdir())) == 1
    status(ctx)
    assert list(store.graphs_root(ctx.environ, ctx.home).iterdir()) == []
