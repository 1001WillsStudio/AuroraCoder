"""Regression: Settings → Default Model must drive the sidebar picker.

Explorer (fresh page load): pick a non-default model in the sidebar, open
Settings, set Default Model to DeepSeek, Save, Close, then + New Chat and
reload. Settings still showed DeepSeek, but the sidebar stayed on the
last-used provider (Mock LLM, or a leftover custom id).

Cause: App.jsx restored localStorage ``selectedProvider`` whenever that id
was still in the list, and + New Chat never reset the picker. The gateway
already returns the saved default as ``GET /api/providers`` → ``default``.

There is no JS test runner here, so behaviour is locked two ways:
  * ``pickSidebarProvider`` in ``frontend/src/utils/sidebarProvider.js`` is
    executed with Node (hermetic: no network, no DOM);
  * a source scan asserts load and New Chat use that helper and do not
    prefer last-used over ``data.default``.
  * ``get_default_model_entry`` maps the saved Settings value to the same
    picker id the frontend consumes.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gateway import provider_registry, settings_store

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "frontend" / "src" / "utils" / "sidebarProvider.js"
APP = ROOT / "frontend" / "src" / "App.jsx"
SETTINGS = ROOT / "frontend" / "src" / "components" / "SettingsPanel.jsx"


def _eval_js(expr: str):
    script = (
        "import { pickSidebarProvider } from "
        f"{json.dumps(HELPER.resolve().as_uri())}\n"
        f"const out = {expr}\n"
        "console.log(JSON.stringify(out))\n"
    )
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout or "node helper failed")
    return json.loads(proc.stdout)


PROVIDERS = [
    {"id": "deepseek", "name": "DeepSeek"},
    {"id": "mock-model", "name": "Mock LLM (test double)"},
    {"id": "xss", "name": "<img src=x onerror=alert(1)>XSS"},
]


@pytest.mark.unit
def test_saved_default_wins_over_last_used_sidebar_pick():
    """Reported case: Default Model=DeepSeek, last-used is a custom/mock id."""
    providers = json.dumps(PROVIDERS)
    assert _eval_js(f"pickSidebarProvider({providers}, 'deepseek', 'xss')") == "deepseek"
    assert (
        _eval_js(f"pickSidebarProvider({providers}, 'deepseek', 'mock-model')")
        == "deepseek"
    )


@pytest.mark.unit
def test_new_chat_ignores_last_used_and_applies_default():
    """+ New Chat must not keep the previous sidebar selection."""
    providers = json.dumps(PROVIDERS)
    assert _eval_js(f"pickSidebarProvider({providers}, 'deepseek', null)") == "deepseek"


@pytest.mark.unit
def test_last_used_is_only_a_fallback_when_default_is_gone():
    providers = json.dumps(PROVIDERS)
    assert _eval_js(f"pickSidebarProvider({providers}, 'retired', 'mock-model')") == "mock-model"
    assert _eval_js(f"pickSidebarProvider({providers}, null, 'mock-model')") == "mock-model"
    assert _eval_js("pickSidebarProvider([], null, null)") is None
    assert _eval_js(f"pickSidebarProvider({providers}, null, null)") == "deepseek"


@pytest.mark.unit
def test_app_applies_api_default_on_load_not_localstorage():
    """The old success path preferred localStorage over data.default."""
    src = APP.read_text(encoding="utf-8")
    assert "from './utils/sidebarProvider.js'" in src or 'from "./utils/sidebarProvider.js"' in src
    assert "pickSidebarProvider" in src

    start = src.index("async function loadProviders")
    success = src[start : src.index("} catch", start)]
    assert "pickSidebarProvider(" in success
    assert "data.default" in success
    # Last-used must not win when the API returned a default.
    assert "setSelectedProvider(savedProvider)" not in success


@pytest.mark.unit
def test_new_chat_resets_sidebar_picker_to_default_model():
    src = APP.read_text(encoding="utf-8")
    start = src.index("const handleClear")
    clear = src[start : src.index("const handleStop", start)]
    assert "pickSidebarProvider" in clear
    assert "setSelectedProvider" in clear
    assert "getProviders()" in clear


@pytest.mark.unit
def test_settings_save_notifies_app_to_reload_providers():
    """Save must tell App to re-read Default Model (not only after 800ms)."""
    src = SETTINGS.read_text(encoding="utf-8")
    start = src.index("await updateSettings")
    after_save = src[start : start + 700]
    assert "providers-changed" in after_save
    # Immediate dispatch — the picker must not wait on the 800ms refresh.
    assert after_save.index("providers-changed") < after_save.index("setTimeout")


@pytest.fixture
def isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings_store, "DATA_DIR", data_dir)
    monkeypatch.setattr(settings_store, "SETTINGS_PATH", data_dir / "settings.json")
    return data_dir


@pytest.mark.unit
def test_providers_default_follows_saved_agent_default_model(isolated_settings):
    """Settings persist; GET /api/providers `default` is the sidebar entry id."""
    settings_store.update_settings(
        {"other": {"agent": {"default_model": {"provider": "deepseek", "model": ""}}}}
    )
    entry_id = provider_registry.get_default_model_entry()
    avail = {e["id"]: e for e in provider_registry.get_available_providers()}
    assert entry_id in avail
    assert avail[entry_id]["provider_id"] == "deepseek"

    settings_store.update_settings(
        {"other": {"agent": {"default_model": {"provider": "nvidia", "model": ""}}}}
    )
    entry_id = provider_registry.get_default_model_entry()
    avail = {e["id"]: e for e in provider_registry.get_available_providers()}
    assert entry_id in avail
    assert avail[entry_id]["provider_id"] == "nvidia"
