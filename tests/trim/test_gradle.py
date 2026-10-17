"""Gradle cases ported from the reference tool (see THIRD_PARTY_NOTICES.md)."""

from pathlib import Path

import pytest

from ai_usage_kit.trim.filters.gradle import detect_task, filter_gradle_build, keep_build_line

FIXTURE = Path(__file__).parent / "fixtures" / "gradle_build_raw.txt"


@pytest.mark.parametrize(
    ("args", "task"),
    [
        (["connectedDebugAndroidTest"], "connected"),
        (["assembleDebug"], "build"),
        (["testDebugUnitTest"], "test"),
        ([":app:testDebugUnitTest"], "test"),
        ([":app:assembleDebug"], "build"),
        (["assembleRelease", "-Pflavor=testRelease"], "build"),
        (["clean", "assembleDebug"], "build"),
        (["lint"], "lint"),
        (["ktlintCheck"], "lint"),
        (["bundleRelease"], "build"),
        (["signingReport"], "other"),
        (["clean"], "build"),
        (["installDebug"], "build"),
        (["uninstallDebug"], "build"),
        (["clean", "installDebug"], "build"),
        (["check"], "test"),
        (["dependencies"], "dependencies"),
        ([":app:dependencies"], "dependencies"),
    ],
)
def test_detect_task(args: list[str], task: str) -> None:
    assert detect_task(args) == task


def _kept(text: str) -> list[str]:
    return [line for line in text.splitlines() if keep_build_line(line)]


def test_success_strips_task_lines() -> None:
    text = "> Configure project :app\n" + "".join(f"> Task :app:t{i} UP-TO-DATE\n" for i in range(11))
    text += "\nBUILD SUCCESSFUL in 1m 23s\n42 actionable tasks: 42 executed"
    kept = _kept(text)
    assert any("BUILD SUCCESSFUL" in line for line in kept)
    assert not any(line.startswith("> Task :") for line in kept)
    assert len("\n".join(kept)) <= 0.3 * len(text)


def test_failure_keeps_errors_strips_try() -> None:
    text = """> Task :app:compileDebugKotlin FAILED

FAILURE: Build failed with an exception.

* What went wrong:
e: /src/app/MainActivity.kt: (42, 5): Unresolved reference: MyService

* Try:
> Run with --stacktrace option to get the stack trace.
> Run with --info or --debug option to get more log output.
> Get more help at https://help.gradle.org

BUILD FAILED in 12s"""
    kept = "\n".join(_kept(text))
    assert "Unresolved reference" in kept and "BUILD FAILED" in kept
    assert "Run with --stacktrace" not in kept and "Get more help at" not in kept


def test_never_empty_on_success() -> None:
    text = "> Task :app:assembleDebug UP-TO-DATE\nBUILD SUCCESSFUL in 3s\n1 actionable tasks: 1 up-to-date"
    assert _kept(text)


def test_daemon_lines_stripped() -> None:
    text = (
        "Starting a Gradle Daemon (subsequent builds will be faster)\n"
        "Daemon will be stopped at the end of the build after running out of JVM memory\n"
        "> Task :app:assembleDebug\nBUILD SUCCESSFUL in 5s"
    )
    kept = "\n".join(_kept(text))
    assert "Daemon" not in kept and "BUILD SUCCESSFUL" in kept


def test_build_scan_url_kept() -> None:
    kept = "\n".join(
        _kept(
            "> Task :app:assembleDebug\nBUILD SUCCESSFUL in 5s\nPublishing build scan...\nhttps://gradle.com/s/abc123"
        )
    )
    assert "gradle.com/s/" in kept


def test_compiler_warnings_kept() -> None:
    text = (
        "> Task :app:compileDebugKotlin\nw: /src/Foo.kt: (42, 5): Parameter 'unused' is never used\n"
        "warning: [options] bootstrap class path not set\nWarning: Gradle deprecation detected\n\n"
        "BUILD SUCCESSFUL in 4s"
    )
    kept = "\n".join(_kept(text))
    for text_part in ("w: ", "warning: [options]", "Warning: Gradle", "BUILD SUCCESSFUL"):
        assert text_part in kept
    assert "> Task :" not in kept


def test_blank_lines_kept() -> None:
    assert keep_build_line("") and keep_build_line("   ")


def test_fixture() -> None:
    raw = FIXTURE.read_text(encoding="utf-8")
    out = filter_gradle_build(raw)
    assert "BUILD SUCCESSFUL" in out and "actionable tasks" in out
    assert "> Task :" not in out
    assert len(out) <= 0.3 * len(raw)
