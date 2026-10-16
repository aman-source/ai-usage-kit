"""eai init / status / on / off / uninstall.

Apply the defaults items to ~/.claude/settings.json and undo them exactly.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import time
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ai_usage_kit.analyze.rules import Thresholds, big_instructions
from ai_usage_kit.defaults.items import (
    ITEMS,
    Item,
    api_billing,
    apply_item,
    get_item,
    hook_installed,
    remove_item,
)
from ai_usage_kit.defaults.managed import Managed
from ai_usage_kit.defaults.settings import (
    Settings,
    SettingsError,
    dump,
    get_path,
    load_settings,
    write_atomic,
)
from ai_usage_kit.hook_files import private_dir, prune_counts
from ai_usage_kit.hook_files import read_counts as read_count_file
from ai_usage_kit.read_once import install as read_once_install
from ai_usage_kit.read_once import state as read_once_state
from ai_usage_kit.trim import install as trim_install
from ai_usage_kit.trim.hook import expire_logs, read_counts, state_dir
from ai_usage_kit.trim.install import hook_command_for

STATE_DIR = ".ai-usage-kit"
KEPT_BACKUP = "settings.json.ai-usage-kit-backup"
DRY_RUN = "Dry run: nothing was written."
HOOK_GROUPS = {"trim": trim_install.hook_groups, "read-once": read_once_install.hook_groups}
EFFORT_TIP = "Effort: change it any time with /effort in Claude Code, or start with: claude --effort high"
UNDO_TIP = "Undo everything with: eai uninstall"
INSTALL_TIP = (
    "error: this item needs eai installed permanently (a project .venv or uvx copy would break later); "
    "run: uv tool install ai-usage-kit"
)
UNBLOCK_TIP = "If Claude needs to read a blocked folder, turn that item off, e.g.: eai off deny-reads"
CHANGED = (
    "settings.json changed while eai was running (Claude Code may have saved it); nothing was written. "
    "Run the command again."
)
CONSOLE_NOTE = (
    "Settings pushed from the claude.ai admin console are not visible here; "
    "run /status in Claude Code to see every source."
)
OUTCOME_TEXT = {
    "applied": "on: {summary}",
    "already": "on (its rules were already there)",
    "own-value": "skipped: you already set {key} yourself",
    "organization": "skipped: set by your organization",
}


@dataclass(frozen=True)
class Context:
    home: Path
    environ: Mapping[str, str]
    managed: Managed
    cwd: Path
    eai_path: str | None = None

    @property
    def settings_path(self) -> Path:
        config_dir = self.environ.get("CLAUDE_CONFIG_DIR")
        base = Path(config_dir).expanduser() if config_dir else self.home / ".claude"
        return base / "settings.json"

    @property
    def state_dir(self) -> Path:
        return self.home / STATE_DIR

    @property
    def state_path(self) -> Path:
        return self.state_dir / "state.json"

    @property
    def backup_path(self) -> Path:
        return self.state_dir / "backup" / "settings.json"

    @property
    def absent_marker(self) -> Path:
        return self.state_dir / "backup" / "settings.json.absent"


@dataclass
class Result:
    lines: list[str] = field(default_factory=list)
    code: int = 0


@dataclass
class _State:
    applied: dict[str, dict[str, Any]]
    off: list[str]


def init(ctx: Context, dry_run: bool = False) -> Result:
    settings, snapshot = _load(ctx)
    state = _load_state(ctx)
    result = Result([DRY_RUN] if dry_run else [])
    new = settings
    rules_to_write: list[Item] = []
    for item in ITEMS:
        if item.id in state.applied:
            record = state.applied[item.id]
            if "rule_sha256" in record:
                rule_state = _rule_state(ctx, record)
                current = _rule_record(item)
                if rule_state == "changed":
                    result.lines.append(_line(item, "on; you changed its file, eai leaves it as is"))
                    continue
                if rule_state == "present" and record == current:
                    result.lines.append(_line(item, "on"))
                    continue
                state.applied[item.id] = current
                rules_to_write.append(item)
                if rule_state == "missing":
                    verb = "would restore" if dry_run else "restored"
                    result.lines.append(_line(item, f"on ({verb}: the file was missing)"))
                else:
                    verb = "would update" if dry_run else "updated"
                    result.lines.append(_line(item, f"on ({verb} to the current text)"))
                continue
            if _presence(new, record) != "missing":
                result.lines.append(_line(item, "on"))
                continue
            concrete = _with_value(item, ctx, record) or item
            change = apply_item(new, concrete, ctx.managed)
            if change.record is not None:
                state.applied[item.id] = _merge_records(record, change.record)
            new = change.settings
            verb = "would restore" if dry_run else "restored"
            result.lines.append(_line(item, f"on ({verb}: it was missing from settings.json)"))
        elif item.id in state.off:
            result.lines.append(_line(item, f"off (turn on with: eai on {item.id})"))
        elif not item.default_on:
            result.lines.append(_line(item, "off: opt-in, see eai status"))
        else:
            change = apply_item(new, item, ctx.managed)
            result.lines.append(_line(item, _outcome(item, change.outcome, dry_run)))
            if change.record is not None:
                state.applied[item.id] = change.record
            new = change.settings
    result.lines += [*_instruction_warnings(ctx), EFFORT_TIP, UNBLOCK_TIP, UNDO_TIP]
    if not dry_run:
        _commit(ctx, settings, new, state, snapshot)  # state first, then any rule files
        for item in rules_to_write:
            write_atomic(_rule_path(ctx, item.key), item.value.encode("utf-8"))
    return result


def status(ctx: Context) -> Result:
    settings, _ = _load(ctx)
    state = _load_state(ctx)
    result = Result([f"Settings file: {ctx.settings_path}"])
    result.lines += [_line(item, _status_text(ctx, settings, state, item)) for item in ITEMS]
    if ctx.managed.sources:
        result.lines.append("Organization settings found: " + ", ".join(ctx.managed.sources))
    unreadable = ctx.managed.unreadable
    result.lines += [f"warning: could not read organization settings at {label}" for label in unreadable]
    trim_state = state_dir(ctx.environ, ctx.home)
    expire_logs(trim_state, time.time())
    runs, before, after = read_counts(trim_state, time.time())
    if runs:
        result.lines.append(
            f"trim shortened {runs} output{'s' if runs != 1 else ''} in the last 30 days: "
            f"{before:,} -> {after:,} characters"
        )
    read_once_state.expire(trim_state, time.time())
    for counts in (trim_state / "trim" / "counts.jsonl", read_once_state.counts_file(trim_state)):
        prune_counts(counts, time.time())
    reads, chars, _ = read_count_file(read_once_state.counts_file(trim_state), time.time())
    if reads:
        result.lines.append(
            f"read-once skipped {reads} repeated read{'s' if reads != 1 else ''} in the last 30 days: "
            f"{chars:,} bytes not re-sent"
        )
    result.lines += [CONSOLE_NOTE, *_instruction_warnings(ctx)]
    return result


def turn_on(ctx: Context, item_id: str, dry_run: bool = False) -> Result:
    item = get_item(item_id)
    settings, snapshot = _load(ctx)
    state = _load_state(ctx)
    if item.id in state.applied:
        return Result([_line(item, "already on")])
    if item.rule_file:
        return _turn_on_rule(ctx, item, settings, state, snapshot, dry_run)
    if item.needs_api_billing and api_billing(ctx.environ, settings) is None:
        message = f"error: {item.id} only helps with API-key or cloud-provider billing; none was detected"
        return Result([message], 1)
    if item.needs_hook_command:
        blocked = _hook_block(ctx, settings)
        if blocked:
            return Result([f"error: {item.id} cannot run: {blocked}"], 1)
    concrete = _with_value(item, ctx)
    if concrete is None:
        return Result([INSTALL_TIP], 1)
    change = apply_item(settings, concrete, ctx.managed)
    if item.id in state.off:
        state.off.remove(item.id)
    if change.record is not None:
        state.applied[item.id] = change.record
    result = Result([*([DRY_RUN] if dry_run else []), _line(item, _outcome(item, change.outcome, dry_run))])
    if not dry_run:
        _commit(ctx, settings, change.settings, state, snapshot)
    return result


def turn_off(ctx: Context, item_id: str, dry_run: bool = False) -> Result:
    item = get_item(item_id)
    settings, snapshot = _load(ctx)
    state = _load_state(ctx)
    result = Result([DRY_RUN] if dry_run else [])
    new = settings
    record = state.applied.pop(item.id, None)
    rule_to_delete: Path | None = None
    off = "would turn off" if dry_run else "off"
    if record is None:
        result.lines.append(_line(item, "off (it was not on)"))
    elif "rule_sha256" in record:
        path = _rule_path(ctx, record["key"])
        if _rule_state(ctx, record) == "changed":
            result.lines.append(_line(item, f"{off}; kept your edited file {path}"))
        else:
            result.lines.append(_line(item, off))
            rule_to_delete = path
    else:
        change = remove_item(settings, record)
        new = change.settings
        kept = change.outcome == "changed-by-you"
        result.lines.append(_line(item, f"{off}; kept your own {item.key} value" if kept else off))
    if item.id not in state.off:
        state.off.append(item.id)
    if not dry_run:
        _commit(ctx, settings, new, state, snapshot)  # state first, then the file
        if rule_to_delete is not None:
            rule_to_delete.unlink(missing_ok=True)
        if item.needs_hook_command:
            _delete_saved_outputs(ctx, item.id)
    return result


def uninstall(ctx: Context, dry_run: bool = False) -> Result:
    if not ctx.state_dir.exists():
        return Result(["Nothing to uninstall: ai-usage-kit has not changed your settings."])
    settings, snapshot = _load(ctx)
    state = _load_state(ctx)
    result = Result([DRY_RUN] if dry_run else [])
    new = settings
    # Undo in reverse order, so an item that created a list is undone after items that added to it.
    rules_to_delete: list[Path] = []
    for item_id, record in reversed(list(state.applied.items())):
        if "rule_sha256" in record:
            path = _rule_path(ctx, record["key"])
            rule_state = _rule_state(ctx, record)
            if rule_state == "changed":
                result.lines.append(_label(item_id, f"left as is: you changed {path} after install"))
                continue
            if rule_state == "present":
                rules_to_delete.append(path)
            result.lines.append(_label(item_id, "would remove" if dry_run else "removed"))
            continue
        change = remove_item(new, record)
        new = change.settings
        changed = change.outcome == "changed-by-you"
        removed = "would remove" if dry_run else "removed"
        text = f"left as is: you changed {record['key']} after install" if changed else removed
        result.lines.append(_label(item_id, text))
    if not dry_run:
        _check_unchanged(ctx, snapshot)
    result.lines.append(_finish_uninstall(ctx, settings, new, dry_run))
    if not dry_run:
        for path in rules_to_delete:
            path.unlink(missing_ok=True)
            with contextlib.suppress(OSError):
                path.parent.rmdir()  # only when eai's files were the last ones in it
        shutil.rmtree(ctx.state_dir)
    return result


def _finish_uninstall(ctx: Context, settings: Settings, new: Settings, dry_run: bool) -> str:
    original = ctx.backup_path.read_bytes() if ctx.backup_path.exists() else None
    if not ctx.settings_path.exists():
        if original is None:
            return "Nothing to restore: settings.json does not exist, as before install."
        kept = ctx.settings_path.with_name(KEPT_BACKUP)
        if not dry_run:
            kept.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ctx.backup_path, kept)
            os.chmod(kept, 0o600)
        return (
            "settings.json was deleted after install, so it was left deleted. "
            f"Your original file is saved at {kept} "
            "(it may hold secrets such as API keys; delete it when you no longer need it)"
        )
    if original is not None and _parse(original) == new:
        if not dry_run:
            write_atomic(ctx.settings_path, original)
        return "Restored your original settings.json exactly."
    if ctx.absent_marker.exists() and not new:
        if not dry_run:
            ctx.settings_path.unlink(missing_ok=True)
        return "Removed settings.json (it did not exist before)."
    if not dry_run and new != settings:
        _write_settings(ctx, new)
    if original is None:
        return "Removed our settings; your other settings are unchanged."
    kept = ctx.settings_path.with_name(KEPT_BACKUP)
    if not dry_run:
        shutil.copyfile(ctx.backup_path, kept)
        os.chmod(kept, 0o600)
    return (
        f"Removed our settings and kept your later edits. Your original file is saved at {kept} "
        "(it may hold secrets such as API keys; delete it when you no longer need it)"
    )


def _status_text(ctx: Context, settings: Settings, state: _State, item: Item) -> str:
    if item.needs_hook_command:
        blocked = _hook_block(ctx, settings)
        if blocked:
            prefix = "on, but it cannot run: " if item.id in state.applied else "blocked: "
            return prefix + blocked
    if item.rule_file and item.id not in state.applied and _is_eai_rule_file(ctx, item):
        return f"off, but its file is still there: delete {_rule_path(ctx, item.key)}"
    if item.id in state.applied and "rule_sha256" in state.applied[item.id]:
        rule_state = _rule_state(ctx, state.applied[item.id])
        if rule_state == "missing":
            return "on in eai's records but the file is missing; restore it with: eai init"
        if rule_state == "changed":
            return f"you changed {_rule_path(ctx, item.key)} after install; eai leaves it as is"
        return f"on: {item.summary}"
    if item.id in state.applied:
        presence = _presence(settings, state.applied[item.id])
        if presence == "missing":
            return "on in eai's records but missing from settings.json; restore it with: eai init"
        if presence == "changed":
            return f"you changed {item.key} after install; eai leaves it as is"
        return f"on: {item.summary}{_override_note(settings, item)}"
    if item.id in state.off:
        return f"off (turn on with: eai on {item.id})"
    if not item.default_on and not item.needs_api_billing:
        return f"off: opt-in, turn on with: eai on {item.id}"
    if item.needs_api_billing:
        billing = api_billing(ctx.environ, settings)
        if billing is None:
            return "not applicable: helps only with API-key or cloud-provider billing"
        return (
            f"off: opt-in ({billing} detected). Helps only if you pause mid-session; check the cache-rebuild "
            f"line in eai analyze, then: eai on {item.id}"
        )
    if not item.is_list and ctx.managed.sets(item.key):
        return "set by your organization"
    if not item.is_list and get_path(settings, item.key)[0]:
        return f"skipped: you set {item.key} yourself"
    return "not installed (run: eai init)"


def _rule_path(ctx: Context, key: str) -> Path:
    return ctx.settings_path.parent / key


def _rule_state(ctx: Context, record: dict[str, Any]) -> str:
    """"present", "missing" or "changed" for a rule file eai wrote."""
    try:
        current = _rule_path(ctx, record["key"]).read_bytes()
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "changed"  # unreadable (a folder, locked): never treat it as eai's file
    return "present" if hashlib.sha256(current).hexdigest() == record["rule_sha256"] else "changed"


def _rule_record(item: Item) -> dict[str, Any]:
    return {"key": item.key, "rule_sha256": hashlib.sha256(item.value.encode("utf-8")).hexdigest()}


def _is_eai_rule_file(ctx: Context, item: Item) -> bool:
    try:
        expected: bytes = item.value.encode("utf-8")
        return _rule_path(ctx, item.key).read_bytes() == expected
    except OSError:
        return False


def _turn_on_rule(
    ctx: Context, item: Item, settings: Settings, state: _State, snapshot: bytes | None, dry_run: bool
) -> Result:
    path = _rule_path(ctx, item.key)
    data = item.value.encode("utf-8")
    try:
        current: bytes | None = path.read_bytes()
    except FileNotFoundError:
        current = None
    except OSError:
        current = b""  # a folder or an unreadable file: someone else's; refuse below
    if current is not None and current != data:
        return Result([f"error: {path} already exists with other content; eai leaves it alone"], 1)
    state.applied[item.id] = {"key": item.key, "rule_sha256": hashlib.sha256(data).hexdigest()}
    if item.id in state.off:
        state.off.remove(item.id)
    verb = "would turn on" if dry_run else "on"
    result = Result([*([DRY_RUN] if dry_run else []), _line(item, f"{verb}: {item.summary}; file {path}")])
    if not dry_run:
        _commit(ctx, settings, settings, state, snapshot)  # state first, then the file
        if current is None:
            write_atomic(path, data)
    return result


def _hook_block(ctx: Context, settings: Settings) -> str | None:
    if ctx.managed.get("allowManagedHooksOnly") is True:
        return "your organization allows only its own hooks"
    if ctx.managed.get("disableAllHooks") is True or settings.get("disableAllHooks") is True:
        return "hooks are turned off (disableAllHooks)"
    return None


def _with_value(item: Item, ctx: Context, record: dict[str, Any] | None = None) -> Item | None:
    """The item with its concrete value.

    A hook item gets this machine's hook entries (or, when restoring, exactly the entries eai added before);
    a list item being restored gets exactly what eai added before.
    """
    if record is not None and isinstance(record.get("events"), dict):
        return replace(item, value=dict(record["events"]))
    if record is not None and "added" in record:
        return replace(item, value=tuple(record["added"]))
    if item.needs_hook_command:
        if ctx.eai_path is None:
            return None
        return replace(item, value=HOOK_GROUPS[item.id](hook_command_for(ctx.eai_path, item.id)))
    return item


def _outcome(item: Item, outcome: str, dry_run: bool = False) -> str:
    text = OUTCOME_TEXT[outcome].format(summary=item.summary, key=item.key)
    return f"would turn {text}" if dry_run and outcome == "applied" else text


def _line(item: Item, text: str) -> str:
    return _label(item.id, text)


def _presence(settings: Settings, record: dict[str, Any]) -> str:
    """"present", "missing" or "changed": does settings.json still hold what eai added?"""
    if "hook_command" in record:
        command = record["hook_command"]
        events = record.get("events")
        if not isinstance(events, dict):
            return "present" if hook_installed(settings, record["key"], command) else "missing"
        present = all(hook_installed(settings, f"hooks.{e}", command) for e in events)
        return "present" if present else "missing"
    found, current = get_path(settings, record["key"])
    if "added" in record:
        rules = current if found and isinstance(current, list) else []
        return "missing" if any(rule not in rules for rule in record["added"]) else "present"
    if not found:
        return "missing"
    return "present" if current == record["value"] else "changed"


def _merge_records(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Combine a restored record with the original: keep everything eai created the first time."""
    if "hook_command" in old:
        created = list(dict.fromkeys([*old.get("created_events", []), *new.get("created_events", [])]))
        parent = bool(old.get("created_parent")) or bool(new.get("created_parent"))
        return {**new, "created_events": created, "created_parent": parent}
    if "added" not in old:
        return new
    added = list(old["added"]) + [rule for rule in new["added"] if rule not in old["added"]]
    return {**old, "added": added}


def _override_note(settings: Settings, item: Item) -> str:
    if item.is_list:
        return ""
    per_model = settings.get("modelSettings")
    if not isinstance(per_model, dict):
        return ""
    models = sorted(
        name for name, value in per_model.items() if isinstance(value, dict) and item.key in value
    )
    return f" (a per-model setting overrides it for: {', '.join(models)})" if models else ""


def _delete_saved_outputs(ctx: Context, item_id: str) -> None:
    """Saved per-session data goes when its item is turned off; the numbers-only counts stay."""
    folder_name = {"trim": "trim", "read-once": "read-once"}.get(item_id)
    if folder_name is None:
        return
    root = state_dir(ctx.environ, ctx.home) / folder_name
    if root.is_dir():
        for folder in root.iterdir():
            if folder.is_dir():
                shutil.rmtree(folder, ignore_errors=True)


def _label(item_id: str, text: str) -> str:
    return f"  {item_id:<11} {text}"


def _instruction_warnings(ctx: Context) -> list[str]:
    findings = big_instructions([str(ctx.cwd)], ctx.home, Thresholds())
    return [f"warning: {f.evidence}; it is sent with every request, consider trimming it" for f in findings]


def _load_state(ctx: Context) -> _State:
    try:
        raw = json.loads(ctx.state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _State({}, [])
    except (OSError, ValueError):
        raise _damaged(ctx) from None
    applied = raw.get("applied") if isinstance(raw, dict) else None
    off = raw.get("off") if isinstance(raw, dict) else None
    if not isinstance(applied, dict) or not isinstance(off, list) or not all(isinstance(i, str) for i in off):
        raise _damaged(ctx)
    if not all(_valid_record(record) for record in applied.values()):
        raise _damaged(ctx)
    return _State(dict(applied), list(off))


def _valid_record(record: object) -> bool:
    if not isinstance(record, dict) or not isinstance(record.get("key"), str):
        return False
    if isinstance(record.get("events"), dict) and isinstance(record.get("hook_command"), str):
        return True
    if isinstance(record.get("rule_sha256"), str):
        return True
    return "value" in record or isinstance(record.get("added"), list)


def _damaged(ctx: Context) -> SettingsError:
    return SettingsError(
        f"{ctx.state_path} is damaged; restore your settings by hand from {ctx.backup_path} if needed"
    )


def _load(ctx: Context) -> tuple[Settings, bytes | None]:
    snapshot = _snapshot(ctx)
    return load_settings(ctx.settings_path) or {}, snapshot


def _snapshot(ctx: Context) -> bytes | None:
    try:
        return ctx.settings_path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise SettingsError(f"cannot read {ctx.settings_path}: {error.strerror or error}") from None


def _check_unchanged(ctx: Context, snapshot: bytes | None) -> None:
    if _snapshot(ctx) != snapshot:
        raise SettingsError(CHANGED)


def _commit(ctx: Context, before: Settings, after: Settings, state: _State, snapshot: bytes | None) -> None:
    _check_unchanged(ctx, snapshot)
    private_dir(ctx.state_dir)
    if after != before:
        _backup_once(ctx)  # only items that change settings need the original copy
    # State first: if the settings write then fails, uninstall still knows what may have been added.
    write_atomic(ctx.state_path, dump({"version": 1, "applied": state.applied, "off": state.off}).encode())
    if after != before:
        _write_settings(ctx, after)


def _backup_once(ctx: Context) -> None:
    if ctx.backup_path.exists() or ctx.absent_marker.exists():
        return
    ctx.backup_path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(ctx.state_dir, 0o700)
    if ctx.settings_path.exists():
        shutil.copyfile(ctx.settings_path, ctx.backup_path)
        os.chmod(ctx.backup_path, 0o600)  # the original may hold secrets such as API keys
    else:
        note = "settings.json did not exist before ai-usage-kit\n"
        ctx.absent_marker.write_text(note, encoding="utf-8")


def _write_settings(ctx: Context, settings: Settings) -> None:
    write_atomic(ctx.settings_path, dump(settings).encode("utf-8"))


def _parse(data: bytes) -> Settings | None:
    try:
        text = data.decode("utf-8-sig")
        parsed = json.loads(text) if text.strip() else {}
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None
