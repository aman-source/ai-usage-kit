import pytest

from ai_usage_kit.mcp_check.toolsearch import effective_env, tool_search


def test_default_is_on() -> None:
    assert tool_search({}).state == "on"


@pytest.mark.parametrize("value", ["false", "FALSE", "0", " false "])
def test_explicit_false_is_off(value: str) -> None:
    result = tool_search({"ENABLE_TOOL_SEARCH": value})
    assert result.state == "off" and "ENABLE_TOOL_SEARCH" in result.reason


def test_gateway_url_turns_it_off_and_hides_the_url() -> None:
    result = tool_search({"ANTHROPIC_BASE_URL": "https://llm.corp.example/v1"})
    assert result.state == "off"
    assert "corp.example" not in result.reason and "gateway" in result.reason


@pytest.mark.parametrize("url", ["https://api.anthropic.com", "https://API.anthropic.com/", "  "])
def test_first_party_or_empty_url_keeps_it_on(url: str) -> None:
    assert tool_search({"ANTHROPIC_BASE_URL": url}).state == "on"


def test_unparsable_url_counts_as_gateway() -> None:
    assert tool_search({"ANTHROPIC_BASE_URL": "not a url"}).state == "off"


def test_explicit_true_overrides_gateway_fallback() -> None:
    result = tool_search({"ANTHROPIC_BASE_URL": "https://llm.corp.example", "ENABLE_TOOL_SEARCH": "true"})
    assert result.state == "on" and "tool_reference" in result.reason


@pytest.mark.parametrize(("value", "percent"), [("auto", "10"), ("auto:5", "5"), ("AUTO:25", "25")])
def test_auto_is_threshold(value: str, percent: str) -> None:
    result = tool_search({"ENABLE_TOOL_SEARCH": value})
    assert result.state == "threshold" and f"{percent}%" in result.reason


def test_disable_experimental_betas_wins() -> None:
    result = tool_search({"CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "1", "ENABLE_TOOL_SEARCH": "true"})
    assert result.state == "off" and "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS" in result.reason


def test_disable_experimental_betas_false_is_ignored() -> None:
    assert tool_search({"CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "0"}).state == "on"


def test_foundry_note() -> None:
    result = tool_search({"CLAUDE_CODE_USE_FOUNDRY": "1"})
    assert result.state == "on" and "Foundry" in result.reason


def test_effective_env_later_blocks_win_and_skip_non_strings() -> None:
    env = effective_env({"A": "shell", "B": "shell"}, [{"A": "user", "C": 3}, {"A": "managed"}])
    assert env == {"A": "managed", "B": "shell"}
