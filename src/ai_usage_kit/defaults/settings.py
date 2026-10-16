"""Read and write Claude Code's settings.json safely: validate, change keys by path, write atomically."""

from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any

Settings = dict[str, Any]


class SettingsError(Exception):
    """A settings file cannot be used as it is; nothing was changed."""


def load_settings(path: Path) -> Settings | None:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise SettingsError(f"cannot read {path}: {error.strerror or error}") from None
    try:
        text = raw.decode("utf-8-sig")  # Windows editors often add a byte order mark
    except UnicodeDecodeError:
        raise SettingsError(f"{path} is not UTF-8 text; save it as UTF-8, then run again") from None
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except ValueError as error:
        raise SettingsError(f"{path} is not valid JSON ({error}); fix it, then run again") from None
    if not isinstance(data, dict):
        raise SettingsError(f"{path} must hold a JSON object")
    return data


def dump(data: Settings) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def get_path(data: Settings, dotted: str) -> tuple[bool, Any]:
    node: Any = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


def set_path(data: Settings, dotted: str, value: Any) -> Settings:
    result = copy.deepcopy(data)
    *parents, last = dotted.split(".")
    node = result
    for part in parents:
        child = node.setdefault(part, {})
        if not isinstance(child, dict):
            raise SettingsError(f"`{part}` in settings.json is not a JSON object; fix it, then run again")
        node = child
    node[last] = copy.deepcopy(value)
    return result


def delete_path(data: Settings, dotted: str, prune: bool = True) -> Settings:
    """Remove a key; with prune, also remove parent objects left empty."""
    result = copy.deepcopy(data)
    *parents, last = dotted.split(".")
    trail: list[tuple[dict[str, Any], str]] = []
    node = result
    for part in parents:
        child = node.get(part)
        if not isinstance(child, dict):
            return result
        trail.append((node, part))
        node = child
    node.pop(last, None)
    for parent, part in reversed(trail if prune else []):
        if parent[part]:
            break
        del parent[part]
    return result
