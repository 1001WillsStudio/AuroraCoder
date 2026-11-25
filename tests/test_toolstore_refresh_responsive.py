import asyncio
import threading
from types import SimpleNamespace

import pytest

from gateway import routes


@pytest.mark.asyncio
@pytest.mark.parametrize("manager_available", [False, True], ids=["without-toolstore", "with-toolstore"])
async def test_toolstore_refresh_does_not_block_gateway_while_process_runs(
    monkeypatch, tmp_path, manager_available
):
    configured_home = tmp_path / "configured"
    fallback_home = tmp_path / "from-environment"
    monkeypatch.setenv("TOOLSTORE_HOME", str(fallback_home))

    def config_manager():
        return SimpleNamespace(config_dir=configured_home)

    monkeypatch.setattr(routes, "ConfigManager", config_manager if manager_available else None)
    expected_home = configured_home if manager_available else fallback_home
    entered = threading.Event()
    release = threading.Event()

    def run(_command, *, env, **_kwargs):
        entered.set()
        release.wait(1)
        assert env["TOOLSTORE_HOME"] == str(expected_home)
        return SimpleNamespace(returncode=0, stdout="Registry updated", stderr="")

    monkeypatch.setattr(routes.subprocess, "run", run)
    refreshing = asyncio.create_task(routes.refresh_toolstore())
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        assert not refreshing.done(), "The gateway loop was blocked until refresh finished"
    finally:
        release.set()
        result = await refreshing
    assert result == {"ok": True, "output": "Registry updated", "errors": None}


@pytest.mark.asyncio
async def test_toolstore_status_reads_fallback_directory_without_toolstore(monkeypatch, tmp_path):
    monkeypatch.setattr(routes, "ConfigManager", None)
    monkeypatch.setenv("TOOLSTORE_HOME", str(tmp_path))
    (tmp_path / "index.json").write_text('{"tools": {"example": {"source": "public"}}}', encoding="utf-8")

    assert await routes.get_toolstore_status() == {
        "total": 1,
        "by_source": {"registry": 1},
        "available": True,
    }
