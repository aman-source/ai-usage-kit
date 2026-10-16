from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Safety net: no test may read or write the real home directory."""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setattr(Path, "home", lambda: home)
    return home
