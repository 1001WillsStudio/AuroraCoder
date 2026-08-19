"""Regression: custom provider Base URL must be an http(s) URL.

Settings used to persist ``not-a-valid-url`` with no error. After reload
the field still showed that value; Discover then reported a missing
http/https protocol and chat failed with a connection error.

These tests lock the store: a non-http(s) Base URL raises and the
on-disk file is left unchanged. Empty / omitted stays allowed (an
incomplete custom provider cannot discover models and stays unused).
"""
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


def _custom(name="QAUrlTest", base_url="https://openrouter.ai/api/v1", api_key="sk-qa-test"):
    return {"id": "custom-qa", "name": name, "base_url": base_url, "api_key": api_key}


def test_update_settings_rejects_non_http_base_url_and_keeps_prior(isolated_settings):
    """Reported case: PUT base_url ``not-a-valid-url`` must not persist."""
    settings_store.update_settings({"custom_providers": [_custom()]})
    assert _read_stored(isolated_settings)["custom_providers"][0]["base_url"] == (
        "https://openrouter.ai/api/v1"
    )

    with pytest.raises(ValueError, match="http"):
        settings_store.update_settings({
            "custom_providers": [_custom(base_url="not-a-valid-url")],
        })

    stored = _read_stored(isolated_settings)
    assert stored["custom_providers"][0]["name"] == "QAUrlTest"
    assert stored["custom_providers"][0]["base_url"] == "https://openrouter.ai/api/v1"


@pytest.mark.parametrize(
    "raw",
    [
        "not-a-valid-url",
        "example.com",
        "ftp://example.com/v1",
        "javascript:alert(1)",
        "https://",
        "http://",
    ],
)
def test_update_settings_rejects_invalid_custom_base_url(isolated_settings, raw):
    with pytest.raises(ValueError, match="http"):
        settings_store.update_settings({"custom_providers": [_custom(base_url=raw)]})
    stored = _read_stored(isolated_settings)
    assert stored.get("custom_providers") in (None, [])


@pytest.mark.parametrize(
    "raw",
    [
        "https://openrouter.ai/api/v1",
        "http://localhost:11434/v1",
        "https://127.0.0.1:8000",
        "  https://api.example.com/v1  ",
    ],
)
def test_update_settings_accepts_http_custom_base_url(isolated_settings, raw):
    result = settings_store.update_settings({"custom_providers": [_custom(base_url=raw)]})
    stored = result["custom_providers"][0]["base_url"]
    assert stored.strip().startswith(("http://", "https://"))


def test_update_settings_allows_empty_custom_base_url(isolated_settings):
    """Blank URL is incomplete, not invalid — do not reject Save."""
    settings_store.update_settings({"custom_providers": [_custom(base_url="")]})
    stored = _read_stored(isolated_settings)
    providers = stored.get("custom_providers") or []
    assert len(providers) == 1
    assert providers[0]["name"] == "QAUrlTest"
    assert not (providers[0].get("base_url") or "").strip()


def test_update_settings_without_custom_providers_is_unchanged(isolated_settings):
    settings_store.update_settings({"other": {"memory": {"enabled": True}}})
    stored = _read_stored(isolated_settings)
    assert stored["other"]["memory"]["enabled"] is True
