"""Cases ported from the reference tool's pytest filter tests (see THIRD_PARTY_NOTICES.md), plus our warnings
section, which the reference drops."""

from ai_usage_kit.trim.filters.pytest import NO_TESTS, filter_pytest, parse_duration, parse_summary_line


def test_all_pass() -> None:
    output = (
        "=== test session starts ===\nplatform darwin -- Python 3.11.0\ncollected 5 items\n\n"
        "tests/test_foo.py .....                                            [100%]\n\n"
        "=== 5 passed in 0.50s ==="
    )
    assert filter_pytest(output) == "Pytest: 5 passed in 0.50s"


def test_with_failures() -> None:
    output = """=== test session starts ===
collected 5 items

tests/test_foo.py ..F..                                            [100%]

=== FAILURES ===
___ test_something ___

    def test_something():
>       assert False
E       assert False

tests/test_foo.py:10: AssertionError

=== short test summary info ===
FAILED tests/test_foo.py::test_something - assert False
=== 4 passed, 1 failed in 0.50s ==="""
    result = filter_pytest(output)
    assert result.startswith("Pytest: 4 passed, 1 failed in 0.50s")
    assert "test_something" in result
    assert "assert False" in result


def test_multiple_failures() -> None:
    output = """=== test session starts ===
collected 3 items

tests/test_foo.py FFF                                              [100%]

=== FAILURES ===
___ test_one ___
E   AssertionError: expected 5

___ test_two ___
E   ValueError: invalid value

=== short test summary info ===
FAILED tests/test_foo.py::test_one - AssertionError: expected 5
FAILED tests/test_foo.py::test_two - ValueError: invalid value
FAILED tests/test_foo.py::test_three - KeyError
=== 3 failed in 0.20s ==="""
    result = filter_pytest(output)
    for text in ("3 failed", "test_one", "test_two", "expected 5"):
        assert text in result


def test_no_tests() -> None:
    output = "=== test session starts ===\ncollected 0 items\n\n=== no tests ran in 0.00s ==="
    assert filter_pytest(output) == NO_TESTS


def test_parse_summary_line() -> None:
    c = parse_summary_line("=== 5 passed in 0.50s ===")
    assert (c.passed, c.failed, c.skipped, c.duration) == (5, 0, 0, "0.50s")
    c = parse_summary_line("=== 4 passed, 1 failed in 0.50s ===")
    assert (c.passed, c.failed, c.skipped, c.duration) == (4, 1, 0, "0.50s")
    c = parse_summary_line("=== 3 passed, 1 failed, 2 skipped in 1.0s ===")
    assert (c.passed, c.failed, c.skipped, c.duration) == (3, 1, 2, "1.0s")
    c = parse_summary_line("=== 2 passed, 1 failed, 2 xfailed, 1 xpassed in 1.0s ===")
    assert (c.passed, c.failed, c.xfailed, c.xpassed, c.duration) == (2, 1, 2, 1, "1.0s")


def test_quiet_and_long_durations() -> None:
    assert filter_pytest("2 passed in 3.04s") == "Pytest: 2 passed in 3.04s"
    assert filter_pytest("2 passed in 65.12s (0:01:05)") == "Pytest: 2 passed in 65.12s"


def test_parse_duration_rejects_non_seconds() -> None:
    assert parse_duration("=== 2 passed in 0.50s ===") == "0.50s"
    assert parse_duration("2 passed in 65.12s (0:01:05)") == "65.12s"
    assert parse_duration("=== 2 passed ===") is None
    assert parse_duration("2 passed in seconds") is None
    assert parse_duration("2 passed in 0.50") is None


def test_duration_on_every_summary_shape() -> None:
    assert filter_pytest("=== 5 passed, 2 warnings in 65.12s (0:01:05) ===") == "Pytest: 5 passed in 65.12s"
    assert filter_pytest("=== 3 skipped in 0.10s ===") == "Pytest: 0 passed, 0 failed, 3 skipped in 0.10s"
    assert (
        filter_pytest("5 failed, 1698 passed, 2 skipped in 108.89s")
        == "Pytest: 1698 passed, 5 failed, 2 skipped in 108.89s"
    )


def test_xfail_cap() -> None:
    lines = "=== test session starts ===\ncollected 30 items\n\ntest_x.py " + "x" * 15
    lines += "\n\n=== short test summary info ===\n"
    lines += "".join(f"XFAIL test_x.py::test_case_{i} - known issue #{i}\n" for i in range(15))
    lines += "=== 0 passed, 15 xfailed in 0.05s ===\n"
    result = filter_pytest(lines)
    section = result.split("Expected-failure outcomes:")[1]
    assert sum(1 for line in section.splitlines() if line.strip().startswith("XFAIL")) <= 10
    assert "\u2026 +5 more" in result


def test_xfail_xpass() -> None:
    output = """=== test session starts ===
collected 5 items

test_math.py ..xxX                                                 [100%]

=== short test summary info ===
XFAIL test_math.py::test_division_by_zero - known bug in division
XFAIL test_math.py::test_float_precision - float precision issue \u2014 bug #42
XPASS test_math.py::test_unexpected_pass - this should fail but currently passes
=== 2 passed, 2 xfailed, 1 xpassed in 0.05s ==="""
    result = filter_pytest(output)
    for text in ("xfailed", "xpassed", "XPASS", "float precision", "test_division_by_zero"):
        assert text in result


def test_quiet_mode_failures() -> None:
    output = """=== test session starts ===
platform linux -- Python 3.12.11, pytest-8.1.0
collected 1705 items

.......F.......

=== FAILURES ===
___ test_something ___

E   AssertionError: expected True

=== short test summary info ===
FAILED tests/test_foo.py::test_something - AssertionError
5 failed, 1698 passed, 2 skipped in 108.89s"""
    result = filter_pytest(output)
    assert NO_TESTS not in result
    assert "1698" in result


def test_only_skipped() -> None:
    output = "=== test session starts ===\ncollected 3 items\n\n=== 3 skipped in 0.10s ==="
    assert NO_TESTS not in filter_pytest(output)


def test_ansi_colored_summary() -> None:
    assert filter_pytest("\x1b[32m=== 5 passed in 0.50s ===\x1b[0m") == "Pytest: 5 passed in 0.50s"


# Our deliberate difference: the warnings section is kept, capped at 30 lines.
def test_warnings_section_is_kept() -> None:
    output = """=== test session starts ===
collected 2 items

tests/test_a.py ..                                                 [100%]

=== warnings summary ===
tests/test_a.py::test_one
  /src/a.py:3: DeprecationWarning: old_api() is deprecated, use new_api()
    old_api()

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
=== 2 passed, 1 warning in 0.10s ==="""
    result = filter_pytest(output)
    assert result.startswith("Pytest: 2 passed in 0.10s")
    assert "Warnings:" in result
    assert "DeprecationWarning: old_api() is deprecated" in result
    assert "-- Docs:" not in result


def test_warnings_section_is_capped() -> None:
    body = "".join(f"  w{i}: DeprecationWarning: thing {i} passed in 1s\n" for i in range(40))
    output = f"=== warnings summary ===\n{body}=== 1 passed, 40 warnings in 0.10s ==="
    result = filter_pytest(output)
    assert result.startswith("Pytest: 1 passed in 0.10s")
    assert "w29:" in result and "w30:" not in result
    assert "... (10 more warning lines)" in result
