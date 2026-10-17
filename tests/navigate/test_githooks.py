import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from ai_usage_kit.navigate import githooks
from tests.navigate.conftest import GIT_ENV, git, init_repo

EAI = "C:/Program Files/x/eai.exe"


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")
    init_repo(root)
    return root


def _hooks(root: Path) -> Path:
    return root / ".git" / "hooks"


def test_block_quotes_paths_with_spaces() -> None:
    block = githooks.block(EAI)
    assert '"C:/Program Files/x/eai.exe" navigate update --background >/dev/null 2>&1 || true' in block
    assert githooks.block("C:\\Users\\a b\\eai.exe").count('"C:/Users/a b/eai.exe"') == 1


def test_install_creates_missing_hooks(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    lines = githooks.install(root, EAI)
    for name in githooks.HOOKS:
        text = (_hooks(root) / name).read_text(encoding="utf-8")
        assert text.startswith("#!/bin/sh\n") and githooks.block(EAI) in text
        if os.name != "nt":
            assert (_hooks(root) / name).stat().st_mode & stat.S_IXUSR
    assert len(lines) == 3 and all("created" in line for line in lines)


def test_existing_shell_hook_keeps_exec_line_and_restores_exactly(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    original = b'#!/bin/bash\n# user hook\nexec some-tool "$@"\n'
    hook = _hooks(root) / "post-merge"
    hook.write_bytes(original)
    githooks.install(root, EAI)
    lines = hook.read_text(encoding="utf-8").split("\n")
    assert lines[0] == "#!/bin/bash" and lines[1] == githooks.START
    assert hook.read_bytes().endswith(b'# user hook\nexec some-tool "$@"\n')
    githooks.remove(root)
    assert hook.read_bytes() == original


def test_crlf_hook(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    original = b"#!/usr/bin/env sh\r\necho hi\r\n"
    hook = _hooks(root) / "post-checkout"
    hook.write_bytes(original)
    githooks.install(root, EAI)
    data = hook.read_bytes()
    assert githooks.START.encode() + b"\r\n" in data and b"\n" not in data.replace(b"\r\n", b"")
    githooks.remove(root)
    assert hook.read_bytes() == original


@pytest.mark.parametrize(
    ("content", "reason"),
    [(b"#!/usr/bin/env python3\nprint(1)\n", "not a shell script"), (b"echo no shebang\n", "no #! line")],
)
def test_other_hooks_are_skipped(tmp_path: Path, content: bytes, reason: str) -> None:
    root = _repo(tmp_path)
    hook = _hooks(root) / "post-rewrite"
    hook.write_bytes(content)
    lines = githooks.install(root, EAI)
    assert hook.read_bytes() == content
    assert any("post-rewrite" in line and "skipped" in line and reason in line for line in lines)


def test_binary_hook_is_skipped(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    hook = _hooks(root) / "post-merge"
    hook.write_bytes(b"#!/bin/sh\n\xff\xfe\x00binary")
    githooks.install(root, EAI)
    assert hook.read_bytes() == b"#!/bin/sh\n\xff\xfe\x00binary"


def test_install_twice_is_idempotent(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    githooks.install(root, EAI)
    lines = githooks.install(root, EAI)
    assert all("already there" in line for line in lines)
    assert (_hooks(root) / "post-merge").read_text(encoding="utf-8").count(githooks.START) == 1


def test_remove_deletes_only_files_eai_created(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    own = _hooks(root) / "post-merge"
    own.write_bytes(b"#!/bin/sh\n")
    githooks.install(root, EAI)
    githooks.remove(root)
    assert own.read_bytes() == b"#!/bin/sh\n"
    assert not (_hooks(root) / "post-checkout").exists()
    assert not (_hooks(root) / "post-rewrite").exists()


def test_created_file_edited_by_user_is_kept(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    githooks.install(root, EAI)
    hook = _hooks(root) / "post-merge"
    hook.write_text(hook.read_text(encoding="utf-8") + "echo mine\n", encoding="utf-8")
    githooks.remove(root)
    assert hook.exists() and githooks.START not in hook.read_text(encoding="utf-8")
    assert "echo mine" in hook.read_text(encoding="utf-8")


def test_hooks_path_inside_working_tree_is_refused(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    git(root, "config", "core.hooksPath", ".husky")
    lines = githooks.install(root, EAI)
    assert "refused" in lines[0]
    assert any("navigate update --background" in line for line in lines)
    assert not (root / ".husky").exists()


def test_shared_hooks_folder_is_refused(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    shared = tmp_path / "shared-hooks"
    git(root, "config", "core.hooksPath", str(shared))
    lines = githooks.install(root, EAI)
    assert "refused" in lines[0] and "shared" in lines[0]
    assert not shared.exists()


def test_status(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    assert all("off" in line for line in githooks.status(root))
    githooks.install(root, EAI)
    assert all(" on" in line for line in githooks.status(root))


def test_not_a_repo(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert "not a git repository" in githooks.install(plain, EAI)[0]
    assert "not a git repository" in githooks.remove(plain)[0]


def test_real_git_runs_the_hook_and_never_fails(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    marker = tmp_path / "ran.txt"
    fake = tmp_path / "fake eai.py"
    fake.write_text(f"import pathlib\npathlib.Path({str(marker)!r}).write_text('x')\nraise SystemExit(1)\n")
    wrapper = tmp_path / "fake eai.sh"
    python = Path(sys.executable).as_posix()
    wrapper.write_text(f'#!/bin/sh\n"{python}" "{fake.as_posix()}"\nexit 1\n', encoding="utf-8")
    wrapper.chmod(0o755)
    githooks.install(root, str(wrapper))
    done = subprocess.run(
        ["git", "-C", str(root), "checkout", "-q", "-b", "other"], capture_output=True, text=True, env=GIT_ENV
    )
    assert done.returncode == 0, done.stderr
    assert marker.exists()


def test_hook_without_trailing_newline_restored_exactly(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    hook = _hooks(root) / "post-merge"
    hook.write_bytes(b"#!/bin/sh")
    githooks.install(root, EAI)
    assert githooks.START in hook.read_text(encoding="utf-8")
    githooks.remove(root)
    assert hook.read_bytes() == b"#!/bin/sh"


@pytest.mark.skipif(os.name == "nt", reason="symlinks need extra rights on Windows")
def test_symlinked_hook_is_skipped(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    target = tmp_path / "real-hook"
    target.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    link = _hooks(root) / "post-merge"
    link.symlink_to(target)
    lines = githooks.install(root, EAI)
    assert link.is_symlink() and target.read_text(encoding="utf-8") == "#!/bin/sh\necho hi\n"
    assert any("post-merge" in line and "skipped" in line and "link" in line for line in lines)


def test_unwritable_hook_is_reported_not_raised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path)

    def denied(path: Path, data: bytes, mode: int) -> None:
        raise PermissionError(13, "Access is denied", str(path))

    monkeypatch.setattr(githooks, "_write", denied)
    lines = githooks.install(root, EAI)
    assert all("skipped: cannot write" in line for line in lines)
