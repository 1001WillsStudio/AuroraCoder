"""Regression: Agent Behavior / Web Secondary numeric ranges on Save.

The Settings spinbuttons advertise min/max (concurrency 1–20, terminal
output 1000–100000, web-secondary tokens 256–32768), but Save used to PUT
the typed strings and the store persisted them. After reload the controls
still showed 50, 1, and 10.

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


def test_update_settings_rejects_concurrency_50_and_keeps_prior(isolated_settings):
    """Reported case: PUT max_tool_concurrency \"50\" must not persist."""
    settings_store.update_settings({"other": {"agent": {"max_tool_concurrency": "5"}}})
    assert _read_stored(isolated_settings)["other"]["agent"]["max_tool_concurrency"] == "5"

    with pytest.raises(ValueError, match="between 1 and 20"):
        settings_store.update_settings({"other": {"agent": {"max_tool_concurrency": "50"}}})

    stored = _read_stored(isolated_settings)
    assert stored["other"]["agent"]["max_tool_concurrency"] == "5"


def test_update_settings_rejects_terminal_output_1_and_keeps_prior(isolated_settings):
    """Reported case: PUT terminal_max_output \"1\" must not persist."""
    settings_store.update_settings({"other": {"agent": {"terminal_max_output": "15000"}}})
    assert _read_stored(isolated_settings)["other"]["agent"]["terminal_max_output"] == "15000"

    with pytest.raises(ValueError, match="between 1000 and 100000"):
        settings_store.update_settings({"other": {"agent": {"terminal_max_output": "1"}}})

    stored = _read_stored(isolated_settings)
    assert stored["other"]["agent"]["terminal_max_output"] == "15000"


def test_update_settings_rejects_web_secondary_tokens_10(isolated_settings):
    """Related check: PUT web_secondary.max_tokens \"10\" must not persist."""
    settings_store.update_settings({"other": {"web_secondary": {"max_tokens": "4096"}}})
    assert _read_stored(isolated_settings)["other"]["web_secondary"]["max_tokens"] == "4096"

    with pytest.raises(ValueError, match="between 256 and 32768"):
        settings_store.update_settings({"other": {"web_secondary": {"max_tokens": "10"}}})

    stored = _read_stored(isolated_settings)
    assert stored["other"]["web_secondary"]["max_tokens"] == "4096"


@pytest.mark.parametrize("raw", ["0", 0, "21", 21, "-1", "abc", 1.5, True])
def test_update_settings_rejects_out_of_range_concurrency(isolated_settings, raw):
    settings_store.update_settings({"other": {"agent": {"edit_mode": "aurora"}}})
    with pytest.raises(ValueError, match="between 1 and 20"):
        settings_store.update_settings({"other": {"agent": {"max_tool_concurrency": raw}}})
    stored = _read_stored(isolated_settings)
    assert "max_tool_concurrency" not in stored.get("other", {}).get("agent", {})


@pytest.mark.parametrize("raw", ["999", 999, "100001", 100001, "0", "abc", 1500.5])
def test_update_settings_rejects_out_of_range_terminal_output(isolated_settings, raw):
    settings_store.update_settings({"other": {"agent": {"edit_mode": "aurora"}}})
    with pytest.raises(ValueError, match="between 1000 and 100000"):
        settings_store.update_settings({"other": {"agent": {"terminal_max_output": raw}}})
    stored = _read_stored(isolated_settings)
    assert "terminal_max_output" not in stored.get("other", {}).get("agent", {})


@pytest.mark.parametrize("raw", ["1", 1, 20, "20", "5", "5.0"])
def test_update_settings_accepts_in_range_concurrency(isolated_settings, raw):
    result = settings_store.update_settings({"other": {"agent": {"max_tool_concurrency": raw}}})
    stored = result.get("other", {}).get("agent", {}).get("max_tool_concurrency")
    assert stored is not None
    assert int(float(stored)) == int(float(raw))


@pytest.mark.parametrize("raw", ["1000", 1000, "15000", 100000, "100000", "1000.0"])
def test_update_settings_accepts_in_range_terminal_output(isolated_settings, raw):
    result = settings_store.update_settings({"other": {"agent": {"terminal_max_output": raw}}})
    stored = result.get("other", {}).get("agent", {}).get("terminal_max_output")
    assert stored is not None
    assert int(float(stored)) == int(float(raw))


@pytest.mark.parametrize("raw", ["256", 256, "4096", 32768, "32768"])
def test_update_settings_accepts_in_range_web_secondary_tokens(isolated_settings, raw):
    result = settings_store.update_settings({"other": {"web_secondary": {"max_tokens": raw}}})
    stored = result.get("other", {}).get("web_secondary", {}).get("max_tokens")
    assert stored is not None
    assert int(float(stored)) == int(float(raw))


def test_update_settings_allows_empty_numeric_fields(isolated_settings):
    """Blank fields mean 'use the default' — do not reject Save."""
    settings_store.update_settings({
        "other": {
            "agent": {"max_tool_concurrency": "", "terminal_max_output": ""},
            "web_secondary": {"max_tokens": ""},
        }
    })
    stored = _read_stored(isolated_settings)
    agent = stored.get("other", {}).get("agent", {})
    ws = stored.get("other", {}).get("web_secondary", {})
    assert "max_tool_concurrency" not in agent
    assert "terminal_max_output" not in agent
    assert "max_tokens" not in ws
