import asyncio
import threading
from types import SimpleNamespace

import pytest

from gateway import routes


@pytest.mark.asyncio
async def test_toolstore_refresh_does_not_block_gateway_while_process_runs(monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    def run(*_args, **_kwargs):
        entered.set()
        release.wait(1)
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
