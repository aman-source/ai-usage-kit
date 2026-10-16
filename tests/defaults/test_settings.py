from pathlib import Path

import pytest

from ai_usage_kit.defaults.settings import (
    SettingsError,
    delete_path,
    dump,
    get_path,
    load_settings,
    set_path,
    write_atomic,
)


def test_missing_file_is_none(tmp_path: Path) -> None:
    assert load_settings(tmp_path / "settings.json") is None


def test_empty_file_is_an_empty_object(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("  \n", encoding="utf-8")
    assert load_settings(path) == {}


def test_invalid_json_is_one_clear_error(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{oops", encoding="utf-8")
    with pytest.raises(SettingsError, match="is not valid JSON"):
        load_settings(path)


def test_non_object_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("[1]", encoding="utf-8")
    with pytest.raises(SettingsError, match="JSON object"):
        load_settings(path)


def test_key_paths_do_not_mutate_input() -> None:
    data = {"a": 1, "permissions": {"allow": ["x"]}}
    out = set_path(data, "permissions.deny", ["r"])
    assert out == {"a": 1, "permissions": {"allow": ["x"], "deny": ["r"]}}
    assert data == {"a": 1, "permissions": {"allow": ["x"]}}
    assert get_path(out, "permissions.deny") == (True, ["r"])
    assert get_path(out, "nope.x") == (False, None)
    assert delete_path(out, "permissions.deny") == data
    assert delete_path({"permissions": {"deny": ["r"]}}, "permissions.deny") == {}
    assert delete_path({"a": 1}, "missing.key") == {"a": 1}


def test_set_path_refuses_non_object_parent() -> None:
    with pytest.raises(SettingsError, match="permissions"):
        set_path({"permissions": "x"}, "permissions.deny", ["r"])


def test_write_atomic_replaces_and_leaves_no_temp_file(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    write_atomic(path, b"one")
    write_atomic(path, b"two")
    assert path.read_bytes() == b"two"
    assert list(tmp_path.iterdir()) == [path]


def test_dump_keeps_key_order_and_unicode() -> None:
    assert dump({"b": 1, "a": "\u00e9"}) == '{\n  "b": 1,\n  "a": "\u00e9"\n}\n'


def test_home_is_isolated_in_tests(_isolate_home: Path) -> None:
    assert Path.home() == _isolate_home
