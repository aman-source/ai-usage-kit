import pytest

from ai_usage_kit.defaults.items import (
    DENY_RULES,
    ITEM_IDS,
    api_billing,
    apply_item,
    get_item,
    remove_item,
)
from ai_usage_kit.defaults.managed import Managed
from ai_usage_kit.defaults.settings import SettingsError

NONE = Managed()
DENY = get_item("deny-reads")


def test_item_ids() -> None:
    assert ITEM_IDS == (
        "compact",
        "effort",
        "deny-reads",
        "deny-deps",
        "cache",
        "trim",
        "read-once",
        "concise",
        "minimal",
        "navigate",
        "subagent-model",
    )


def test_value_item_applies_when_unset() -> None:
    change = apply_item({"model": "x"}, get_item("compact"), NONE)
    assert change.outcome == "applied"
    assert change.settings == {"model": "x", "autoCompactWindow": 200000}
    assert change.record == {"key": "autoCompactWindow", "value": 200000, "created_parent": False}


def test_value_item_keeps_the_developers_choice() -> None:
    change = apply_item({"effortLevel": "high"}, get_item("effort"), NONE)
    assert (change.outcome, change.settings, change.record) == ("own-value", {"effortLevel": "high"}, None)


def test_value_item_skips_what_the_organization_sets() -> None:
    org = Managed(documents=[{"effortLevel": "low"}], sources=["f"])
    change = apply_item({}, get_item("effort"), org)
    assert (change.outcome, change.settings) == ("organization", {})


def test_deny_rules_append_only_missing_ones() -> None:
    settings = {"permissions": {"deny": ["Read(./.env)", "Read(uv.lock)"]}}
    change = apply_item(settings, DENY, NONE)
    deny = change.settings["permissions"]["deny"]
    assert deny[:2] == ["Read(./.env)", "Read(uv.lock)"]
    assert deny.count("Read(uv.lock)") == 1
    assert change.record is not None
    assert "Read(uv.lock)" not in change.record["added"]
    assert change.record["created"] is False


def test_deny_rules_apply_alongside_organization_rules() -> None:
    org = Managed(documents=[{"permissions": {"deny": ["Read(.env)"]}}], sources=["f"])
    assert apply_item({}, DENY, org).outcome == "applied"


def test_deny_rules_already_present() -> None:
    change = apply_item({"permissions": {"deny": list(DENY_RULES)}}, DENY, NONE)
    assert change.outcome == "already"
    expected = {"key": "permissions.deny", "added": [], "created": False, "created_parent": False}
    assert change.record == expected


def test_remove_value_item_only_if_unchanged() -> None:
    record = {"key": "effortLevel", "value": "medium"}
    assert remove_item({"effortLevel": "medium", "a": 1}, record).settings == {"a": 1}
    changed = remove_item({"effortLevel": "high"}, record)
    assert (changed.outcome, changed.settings) == ("changed-by-you", {"effortLevel": "high"})
    assert remove_item({}, record).outcome == "removed"


def test_remove_deny_keeps_the_developers_rules() -> None:
    applied = apply_item({"permissions": {"deny": ["Read(./.env)"]}}, DENY, NONE)
    assert applied.record is not None
    back = remove_item(applied.settings, applied.record)
    assert back.settings == {"permissions": {"deny": ["Read(./.env)"]}}


def test_remove_deny_list_we_created_drops_the_key() -> None:
    applied = apply_item({"a": 1}, DENY, NONE)
    assert applied.record is not None
    assert remove_item(applied.settings, applied.record).settings == {"a": 1}


def test_deny_rules_are_relative_read_patterns() -> None:
    for rule in DENY_RULES:
        assert rule.startswith("Read(") and rule.endswith(")")
        assert not rule[5:].startswith(("/", "~"))


def test_non_object_permissions_is_an_error() -> None:
    with pytest.raises(SettingsError, match="permissions"):
        apply_item({"permissions": "x"}, DENY, NONE)


def test_api_billing_names_the_signal_only() -> None:
    assert api_billing({}, {}) is None
    assert api_billing({"ANTHROPIC_API_KEY": "sk-secret"}, {}) == "ANTHROPIC_API_KEY"
    assert api_billing({"CLAUDE_CODE_USE_BEDROCK": "0"}, {}) is None
    assert api_billing({}, {"env": {"CLAUDE_CODE_USE_VERTEX": "1"}}) == "CLAUDE_CODE_USE_VERTEX"
    assert api_billing({}, {"apiKeyHelper": "/bin/key"}) == "apiKeyHelper"
