"""The defaults items and pure functions that add or remove one item from a settings dict.

Setting names and values are from code.claude.com/docs (settings-reference, permissions, prompt-caching,
env-vars), checked 2026-10-16; see docs/defaults.md.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ai_usage_kit.defaults.managed import Managed
from ai_usage_kit.defaults.settings import Settings, SettingsError, delete_path, get_path, set_path
from ai_usage_kit.rules.texts import CONCISE, MINIMAL

# gitignore patterns relative to the current directory; in deny rules they match at any depth.
# Deny rules win over every project setting and also block cat/head/tail in Bash, so the default set covers
# generated output only. Dependencies, build dirs that may hold source, and lock files are opt-in.
GENERATED_PATTERNS = ("dist/**", "__pycache__/**", ".next/**", "coverage/**")
DEPENDENCY_PATTERNS = (
    "node_modules/**", ".venv/**", "venv/**", "vendor/**", "build/**", "target/**", "package-lock.json",
    "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock", "Cargo.lock",
)
DENY_RULES = tuple(f"Read({pattern})" for pattern in GENERATED_PATTERNS)
DEPENDENCY_RULES = tuple(f"Read({pattern})" for pattern in DEPENDENCY_PATTERNS)

# Signs of API-key, cloud-provider or LLM-gateway billing, where the main conversation gets a
# five-minute cache by default. A gateway must forward the anthropic-beta header for the one-hour
# cache to work.
BILLING_ENV = (
    "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
    "ANTHROPIC_API_KEY", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_USE_ANTHROPIC_AWS", "CLAUDE_CODE_USE_MANTLE",
)


@dataclass(frozen=True)
class Item:
    id: str
    key: str
    value: Any
    default_on: bool
    summary: str
    needs_api_billing: bool = False
    needs_hook_command: bool = False
    rule_file: bool = False

    @property
    def is_list(self) -> bool:
        return isinstance(self.value, tuple)


ITEMS = (
    Item("compact", "autoCompactWindow", 200_000, True, "compact long conversations at 200,000 tokens"),
    Item("effort", "effortLevel", "medium", True, "medium effort unless you chose one"),
    Item("deny-reads", "permissions.deny", DENY_RULES, True, "skip generated output (dist, caches)"),
    Item(
        "deny-deps",
        "permissions.deny",
        DEPENDENCY_RULES,
        False,
        "skip dependency folders, build dirs and lock files (opt-in)",
    ),
    Item(
        "cache",
        "promptCacheTtl",
        "1h",
        False,
        "one-hour prompt cache (API key or cloud provider only)",
        needs_api_billing=True,
    ),
    Item(
        "trim",
        "hooks",
        {},
        False,
        "shorter output from successful test, install and build commands (opt-in)",
        needs_hook_command=True,
    ),
    Item(
        "read-once",
        "hooks",
        {},
        False,
        "skip re-reading files that are unchanged and already in context (opt-in)",
        needs_hook_command=True,
    ),
    Item(
        "concise",
        "rules/eai-concise.md",
        CONCISE,
        False,
        "shorter replies, every technical detail kept (opt-in, next session)",
        rule_file=True,
    ),
    Item(
        "minimal",
        "rules/eai-minimal.md",
        MINIMAL,
        False,
        "smallest complete code change, safety never cut (opt-in, next session)",
        rule_file=True,
    ),
)
ITEM_IDS = tuple(item.id for item in ITEMS)


@dataclass(frozen=True)
class Change:
    outcome: str
    settings: Settings
    record: dict[str, Any] | None


def get_item(item_id: str) -> Item:
    for item in ITEMS:
        if item.id == item_id:
            return item
    raise KeyError(item_id)


def api_billing(environ: Mapping[str, str], settings: Settings) -> str | None:
    """The name of the first sign of API/cloud billing, or None. Values are only checked, never returned."""
    block = settings.get("env")
    env_block: Mapping[str, Any] = block if isinstance(block, dict) else {}
    for name in BILLING_ENV:
        if _truthy(environ.get(name)) or _truthy(env_block.get(name)):
            return name
    if settings.get("apiKeyHelper"):
        return "apiKeyHelper"
    return None


def _truthy(value: object) -> bool:
    return isinstance(value, str) and value.strip().lower() not in ("", "0", "false")


def apply_item(settings: Settings, item: Item, managed: Managed) -> Change:
    if item.needs_hook_command:
        return _apply_hooks(settings, item.value)
    found, current = get_path(settings, item.key)
    if item.is_list:
        if found and not isinstance(current, list):
            raise SettingsError(f"`{item.key}` in settings.json is not a list; fix it, then run again")
        existing = list(current) if found else []
        added = [rule for rule in item.value if rule not in existing]
        parent = item.key.rpartition(".")[0]
        created_parent = bool(parent) and not get_path(settings, parent)[0]
        record = {"key": item.key, "added": added, "created": not found, "created_parent": created_parent}
        if not added:
            return Change("already", settings, record)
        return Change("applied", set_path(settings, item.key, existing + added), record)
    if managed.sets(item.key):
        return Change("organization", settings, None)
    if found:
        return Change("own-value", settings, None)
    return Change("applied", set_path(settings, item.key, item.value), {"key": item.key, "value": item.value})


def remove_item(settings: Settings, record: dict[str, Any]) -> Change:
    key = record["key"]
    found, current = get_path(settings, key)
    if "hook_command" in record:
        return _remove_hook(settings, record)
    if "added" in record:
        if not found or not isinstance(current, list):
            return Change("removed", settings, None)
        kept = [rule for rule in current if rule not in record["added"]]
        if not kept and record.get("created"):
            prune = record.get("created_parent", True)
            return Change("removed", delete_path(settings, key, prune=prune), None)
        return Change("removed", set_path(settings, key, kept), None)
    if not found:
        return Change("removed", settings, None)
    if current != record["value"]:
        return Change("changed-by-you", settings, None)
    return Change("removed", delete_path(settings, key), None)


def hook_installed(settings: Settings, key: str, command: str) -> bool:
    """True if any hook group under `key` still runs `command`."""
    found, groups = get_path(settings, key)
    if not found or not isinstance(groups, list):
        return False
    return any(
        isinstance(handler, dict) and handler.get("command") == command
        for group in groups
        if isinstance(group, dict) and isinstance(group.get("hooks"), list)
        for handler in group["hooks"]
    )


def _apply_hooks(settings: Settings, groups: Mapping[str, dict[str, Any]]) -> Change:
    """Add each event's group unless a handler with our command is already there."""
    command = next(iter(groups.values()))["hooks"][0]["command"]
    created_parent = not get_path(settings, "hooks")[0]
    created_events: list[str] = []
    new = settings
    for event, group in groups.items():
        key = f"hooks.{event}"
        found, current = get_path(new, key)
        if found and not isinstance(current, list):
            raise SettingsError(f"`{key}` in settings.json is not a list; fix it, then run again")
        if not found:
            created_events.append(event)
        if not hook_installed(new, key, command):
            new = set_path(new, key, [*(current if found else []), group])
    record = {"key": "hooks", "hook_command": command, "events": dict(groups),
              "created_events": created_events, "created_parent": created_parent}
    return Change("applied" if new != settings else "already", new, record)


def _remove_hook(settings: Settings, record: dict[str, Any]) -> Change:
    """Remove every handler running our command in our events.

    A group, event list or `hooks` object left empty by that removal is deleted, so turning items off in any
    order leaves no empty containers behind.
    """
    command = record["hook_command"]
    events = record.get("events")
    if not isinstance(events, dict):
        events = {record["key"].removeprefix("hooks."): None} if "." in record["key"] else {}
    new = settings
    removed_any = False
    for event in events:
        key = f"hooks.{event}"
        found, groups = get_path(new, key)
        if not found or not isinstance(groups, list):
            continue
        kept_groups: list[Any] = []
        removed_here = False
        for group in groups:
            handlers = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(handlers, list):
                kept_groups.append(group)
                continue
            kept = [h for h in handlers if not (isinstance(h, dict) and h.get("command") == command)]
            if len(kept) == len(handlers):
                kept_groups.append(group)
                continue
            removed_here = True
            if kept:
                kept_groups.append({**group, "hooks": kept})
        removed_any = removed_any or removed_here
        if kept_groups or not (removed_here or event in record.get("created_events", [])):
            new = set_path(new, key, kept_groups)
        else:
            new = delete_path(new, key, prune=False)
    found, hooks = get_path(new, "hooks")
    if found and hooks == {} and (removed_any or record.get("created_parent")):
        new = delete_path(new, "hooks")
    return Change("removed", new, None)
