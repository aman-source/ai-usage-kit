"""ai-usage-kit: local reports and settings that cut AI coding agent token spend."""

from __future__ import annotations


def __getattr__(name: str) -> str:
    # Looked up lazily so the Claude Code hook does not pay for importlib.metadata on every run.
    if name != "__version__":
        raise AttributeError(name)
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("ai-usage-kit")
    except PackageNotFoundError:  # source tree without an install
        return "0.0.0"
