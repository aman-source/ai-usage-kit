"""Read-only view of organization-managed Claude Code settings visible on this machine.

Sources (docs: code.claude.com/docs/en/managed-settings): managed-settings.d/*.json and
managed-settings.json in the system directory, and the `Settings` registry value under HKLM/HKCU
SOFTWARE\\Policies\\ClaudeCode on Windows. Server-managed settings (claude.ai console) and macOS MDM
profiles are not visible locally.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ai_usage_kit.defaults.settings import get_path

POLICY_KEY = r"SOFTWARE\Policies\ClaudeCode"
RegistryReader = Callable[[], list[tuple[str, str]]]


@dataclass
class Managed:
    documents: list[dict[str, Any]] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)

    def sets(self, dotted: str) -> bool:
        return any(get_path(doc, dotted)[0] for doc in self.documents)

    def get(self, dotted: str) -> object | None:
        for document in self.documents:
            found, value = get_path(document, dotted)
            if found:
                result: object = value
                return result
        return None


def managed_dir(platform: str = sys.platform) -> Path:
    if platform == "win32":
        return Path(r"C:\Program Files\ClaudeCode")
    if platform == "darwin":
        return Path("/Library/Application Support/ClaudeCode")
    return Path("/etc/claude-code")


def read_managed(directory: Path, registry: RegistryReader) -> Managed:
    managed = Managed()
    files = sorted((directory / "managed-settings.d").glob("*.json")) + [directory / "managed-settings.json"]
    admin_present = False
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        except OSError:
            admin_present = True
            managed.unreadable.append(str(path))
            continue
        admin_present = True
        _add(managed, str(path), text)
    entries = registry()
    for label, text in entries:
        if not label.startswith("HKCU"):
            admin_present = True
            _add(managed, label, text)
    # Claude Code reads the user-writable HKCU policy only when no admin source is present.
    for label, text in entries:
        if label.startswith("HKCU") and not admin_present:
            _add(managed, label, text)
    return managed


def _add(managed: Managed, label: str, text: str) -> None:
    try:
        document = json.loads(text)
    except ValueError:
        managed.unreadable.append(label)
        return
    if isinstance(document, dict):
        managed.documents.append(document)
        managed.sources.append(label)
    else:
        managed.unreadable.append(label)


def windows_registry() -> list[tuple[str, str]]:
    if sys.platform != "win32":
        return []
    import winreg

    found: list[tuple[str, str]] = []
    for hive, name in ((winreg.HKEY_LOCAL_MACHINE, "HKLM"), (winreg.HKEY_CURRENT_USER, "HKCU")):
        try:
            with winreg.OpenKey(hive, POLICY_KEY) as key:
                value, _ = winreg.QueryValueEx(key, "Settings")
        except OSError:
            continue
        if isinstance(value, str):
            found.append((f"{name}\\{POLICY_KEY}", value))
    return found


def system_managed() -> Managed:
    return read_managed(managed_dir(), windows_registry)
