"""Regression: custom provider Base URL ``not-a-valid-url`` must not persist."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from gateway import settings_store


@pytest.fixture
def isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings_store, "DATA_DIR", data_dir)
    monkeypatch.setattr(settings_store, "SETTINGS_PATH", data_dir / "settings.json")
    return data_dir


def _read_stored(data_dir: Path) -> dict:
    path = data_dir / "settings.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def test_update_settings_rejects_non_http_base_url_and_keeps_prior(isolated_settings):
    """Reported case: PUT base_url ``not-a-valid-url`` must not persist."""
    good = {
        "id": "custom-qa",
        "name": "QAUrlTest",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "sk-qa-test",
    }
    settings_store.update_settings({"custom_providers": [good]})

    with pytest.raises(ValueError, match="http"):
        settings_store.update_settings({
            "custom_providers": [{**good, "base_url": "not-a-valid-url"}],
        })

    stored = _read_stored(isolated_settings)
    assert stored["custom_providers"][0]["base_url"] == "https://openrouter.ai/api/v1"


def test_update_settings_accepts_http_custom_base_url(isolated_settings):
    settings_store.update_settings({
        "custom_providers": [{
            "id": "custom-qa",
            "name": "QAUrlTest",
            "base_url": "http://localhost:11434/v1",
            "api_key": "sk-qa-test",
        }],
    })
    stored = _read_stored(isolated_settings)
    assert stored["custom_providers"][0]["base_url"] == "http://localhost:11434/v1"
