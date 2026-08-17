"""Regression: Max Iterations Per Turn must stay in [5, 200].

The Settings spinbutton advertises min=5 max=200, but Save used to PUT
``other.agent.max_iterations`` as the string ``"0"`` and the store
persisted it. After reload the control still showed 0.

These tests lock the store: out-of-range values raise and the on-disk
file is left unchanged. Empty / omitted stays allowed (system default).
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


def test_update_settings_rejects_zero_and_keeps_prior_value(isolated_settings):
    """Reported case: PUT max_iterations \"0\" must not persist."""
    settings_store.update_settings({"other": {"agent": {"max_iterations": "30"}}})
    assert _read_stored(isolated_settings)["other"]["agent"]["max_iterations"] == "30"

    with pytest.raises(ValueError, match="between 5 and 200"):
        settings_store.update_settings({"other": {"agent": {"max_iterations": "0"}}})

    stored = _read_stored(isolated_settings)
    assert stored["other"]["agent"]["max_iterations"] == "30"


@pytest.mark.parametrize("raw", ["0", 0, "4", 4, "201", 201, "-1", "abc", 5.5, True])
def test_update_settings_rejects_out_of_range_max_iterations(isolated_settings, raw):
    settings_store.update_settings({"other": {"agent": {"edit_mode": "aurora"}}})
    with pytest.raises(ValueError, match="between 5 and 200"):
        settings_store.update_settings({"other": {"agent": {"max_iterations": raw}}})
    stored = _read_stored(isolated_settings)
    assert "max_iterations" not in stored.get("other", {}).get("agent", {})


@pytest.mark.parametrize("raw", ["5", 5, "30", 30, "200", 200, "5.0"])
def test_update_settings_accepts_in_range_max_iterations(isolated_settings, raw):
    result = settings_store.update_settings({"other": {"agent": {"max_iterations": raw}}})
    stored = result.get("other", {}).get("agent", {}).get("max_iterations")
    assert stored is not None
    assert int(float(stored)) == int(float(raw))


def test_update_settings_allows_empty_max_iterations(isolated_settings):
    """Blank field means 'use the default' — do not reject Save."""
    settings_store.update_settings({"other": {"agent": {"max_iterations": ""}}})
    stored = _read_stored(isolated_settings)
    assert "max_iterations" not in stored.get("other", {}).get("agent", {})


def test_update_settings_without_agent_block_is_unchanged(isolated_settings):
    settings_store.update_settings({"other": {"memory": {"enabled": True}}})
    stored = _read_stored(isolated_settings)
    assert stored["other"]["memory"]["enabled"] is True


def test_clamp_agent_max_iterations_lifts_stored_zero():
    """A leftover 0 from before this fix must not run the agent for 0 turns."""
    assert settings_store.clamp_agent_max_iterations("0") == 5
    assert settings_store.clamp_agent_max_iterations(0) == 5
    assert settings_store.clamp_agent_max_iterations("201") == 200
    assert settings_store.clamp_agent_max_iterations("30") == 30
    assert settings_store.clamp_agent_max_iterations(None) == 30
    assert settings_store.clamp_agent_max_iterations("abc") == 30
