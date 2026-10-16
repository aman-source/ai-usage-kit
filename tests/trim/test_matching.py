import pytest

from ai_usage_kit.trim.matching import match_command


@pytest.mark.parametrize(
    ("command", "name"),
    [
        ("pytest", "pytest"),
        ("pytest -v tests/", "pytest"),
        ("python -m pytest -q", "pytest"),
        ("python3 -m pytest tests", "pytest"),
        ("uv run pytest -v", "pytest"),
        ("pytest.exe -v", "pytest"),
        ("uv sync", "uv"),
        ("uv sync --all-extras", "uv"),
        ("uv pip install -r requirements.txt", "uv"),
        ("npm install", "npm"),
        ("npm i", "npm"),
        ("npm ci", "npm"),
        ("npm run build", "npm"),
        ("./gradlew assembleDebug", "gradle"),
        ("gradle clean build", "gradle"),
        ("gradlew.bat build", "gradle"),
    ],
)
def test_qualifying_commands(command: str, name: str) -> None:
    match = match_command(command)
    assert match is not None and match.name == name


@pytest.mark.parametrize(
    "command",
    [
        "pytest -v | tail -5",
        "pytest && echo ok",
        "pytest; ls",
        "pytest > out.txt",
        "pytest $(cat args)",
        "pytest `cat args`",
        "cd x && npm install",
        "pytest -s",
        "pytest --capture=no",
        "pytest --collect-only",
        "pytest --co",
        "pytest --durations=10",
        "pytest -rA",
        "pytest -rP",
        "pytest --version",
        "pytest -h",
        "pytest --fixtures",
        "pytest --setup-show",
        "pytest --pdb",
        "pip install requests",
        "uv run python app.py",
        "uv add requests",
        "npm test",
        "npm view react",
        "./gradlew test",
        "./gradlew assembleDebug --stacktrace",
        "gradle signingReport",
        "git diff",
        "git status",
        "ls -la",
        "pytest 'unterminated",
        "",
    ],
)
def test_non_qualifying_commands(command: str) -> None:
    assert match_command(command) is None
