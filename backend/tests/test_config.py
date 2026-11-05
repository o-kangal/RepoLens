from pathlib import Path

import pytest

from repolens.config import Settings
from repolens.main import create_app


def test_debug_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REPOLENS_DEBUG", raising=False)

    assert Settings().debug is False


def test_reads_settings_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPOLENS_DEBUG", "true")

    assert Settings().debug is True
    assert create_app().debug is True


def test_empty_value_from_env_example_keeps_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPOLENS_DEBUG", "")

    assert Settings().debug is False


def test_does_not_read_a_dotenv_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("REPOLENS_DEBUG", raising=False)
    (tmp_path / ".env").write_text("REPOLENS_DEBUG=true\n")
    monkeypatch.chdir(tmp_path)

    assert Settings().debug is False
