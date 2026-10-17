from pathlib import Path

from ai_usage_kit.navigate import files
from tests.navigate.conftest import git, init_repo


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (root / "README.md").write_text("hi\n", encoding="utf-8")
    (root / ".gitignore").write_text("ignored.py\nbuild/\n", encoding="utf-8")
    init_repo(root)
    return root


def _names(root: Path, paths: list[Path] | None) -> list[str]:
    assert paths is not None
    return sorted(p.relative_to(root).as_posix() for p in paths)


def test_tracked_and_untracked_python_files(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "pkg" / "new.py").write_text("y = 2\n", encoding="utf-8")
    (root / "ignored.py").write_text("z = 3\n", encoding="utf-8")
    (root / "build").mkdir()
    (root / "build" / "gen.py").write_text("z = 3\n", encoding="utf-8")
    assert _names(root, files.python_files(root)) == ["pkg/a.py", "pkg/new.py"]


def test_secret_looking_files_are_excluded(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    for name in (".env.py", "secret_keys.py", "my_credentials.py", "Secret_Sauce.py"):
        (root / "pkg" / name).write_text("v = 1\n", encoding="utf-8")
    (root / "secrets").mkdir()
    (root / "secrets" / "conf.py").write_text("v = 1\n", encoding="utf-8")
    assert _names(root, files.python_files(root)) == ["pkg/a.py"]


def test_huge_files_are_excluded(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "pkg" / "big.py").write_bytes(b"#" * (files.MAX_BYTES + 1))
    (root / "pkg" / "edge.py").write_bytes(b"#" * files.MAX_BYTES)
    assert _names(root, files.python_files(root)) == ["pkg/a.py", "pkg/edge.py"]


def test_spaces_and_non_ascii_names(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "my dir").mkdir()
    (root / "my dir" / "naïve 日本.py").write_text("v = 1\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "more")
    assert "my dir/naïve 日本.py" in _names(root, files.python_files(root))


def test_deleted_but_tracked_file_is_skipped(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "pkg" / "a.py").unlink()
    assert _names(root, files.python_files(root)) == []


def test_not_a_git_repo(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert files.python_files(plain) is None
    assert files.repo_root(plain) is None
    assert files.git_dir(plain) is None
    assert files.head(plain) is None


def test_repo_root_from_subfolder_and_git_dir(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    found = files.repo_root(root / "pkg")
    assert found is not None and found.resolve() == root.resolve()
    gdir = files.git_dir(root)
    assert gdir is not None and gdir.resolve() == (root / ".git").resolve()


def test_head(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    assert files.head(root) == git(root, "rev-parse", "HEAD")
    empty = tmp_path / "empty"
    empty.mkdir()
    git(empty, "init", "-q")
    assert files.head(empty) is None


def test_stamps(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    paths = files.python_files(root)
    assert paths is not None
    stamps = files.stamps(root, [*paths, root / "gone.py"])
    st = (root / "pkg" / "a.py").stat()
    assert stamps == {"pkg/a.py": (st.st_size, st.st_mtime_ns)}


def test_repo_info_in_one_call(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    info = files.repo_info(root / "pkg")
    assert info is not None
    top, gdir = info
    assert top.resolve() == root.resolve() and gdir.resolve() == (root / ".git").resolve()
    plain = tmp_path / "plain"
    plain.mkdir()
    assert files.repo_info(plain) is None


def test_status_gives_head_and_changes_in_one_call(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "pkg" / "a.py").write_text("x = 2\n", encoding="utf-8")
    (root / "my dir").mkdir()
    (root / "my dir" / "new file.py").write_text("y = 1\n", encoding="utf-8")
    git(root, "mv", "README.md", "README2.md")
    (root / "pkg" / "b.py").write_text("z = 1\n", encoding="utf-8")
    git(root, "add", "pkg/b.py")
    git(root, "commit", "-q", "-m", "b")
    git(root, "mv", "pkg/b.py", "pkg/c.py")
    found = files.status(root)
    assert found is not None
    head, dirty = found
    assert head == git(root, "rev-parse", "HEAD")
    assert set(dirty) == {"pkg/a.py", "my dir/new file.py", "pkg/b.py", "pkg/c.py"}
    assert dirty["pkg/b.py"] is None and isinstance(dirty["pkg/c.py"], list)
    assert dirty == files.dirty_python(root)


def test_status_in_an_empty_repo(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    git(empty, "init", "-q")
    (empty / "a.py").write_text("x = 1\n", encoding="utf-8")
    found = files.status(empty)
    assert found is not None
    assert found[0] is None and set(found[1]) == {"a.py"}


def test_status_not_a_repo(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert files.status(plain) is None
