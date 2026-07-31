"""Shared pytest fixtures for the AuroraCoder unit-test suite (QA layer).

Design goals (QA-driven):
  * Hermeticity: unit tests never touch the real host filesystem, network,
    LLM APIs, Docker, or git push. Everything goes through injectable seams.
  * Determinism: no wall-clock dependence (freezegun), no CWD dependence
    (chdir into tmp_path), no shared global mutable state across tests.
  * Reuse: the codebase already uses an "injected fake client" pattern for the
    memory ops tests; these fixtures codify that same seam for the rest of
    the app so every module test can be written the same way.

Layering markers:
  * @pytest.mark.unit        -> default; fast & isolated (see markers in pyproject)
  * @pytest.mark.integration -> needs real Docker/git/LLM; skipped by default
  * @pytest.mark.smoke       -> assembled-app sanity checks
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import tempfile

import pytest

# ---------------------------------------------------------------------------
# Suite-wide hermeticity (RUN FIRST, before any test module imports src.config).
# ---------------------------------------------------------------------------
# The host shell exports AURORACODER_DOCKER=1, which makes src.config bake
# DATA_DIR=/app/data at first import. Several tests in this suite import
# src.config transitively, so whichever test module is collected first bakes
# /app/data for the whole process; its settings.json then leaks
# memory.enabled=true into tests that expect the disabled-by-default baseline
# (eg. test_memory_toggle). pytest imports this conftest BEFORE any test
# module, so forcing local mode + an isolated data dir here wins the import
# race regardless of collection order.
# Use a FRESH mkdtemp per run (never a fixed path: a fixed path accumulates state
# between runs and re-introduces the host-leak problem this exists to solve).
# Seed it with memory enabled=True so the layer1/layer2/layer3 gateway and ops
# suites (which expect the enabled path) behave exactly as they did against the
# host /app/data, while test_memory_toggle flips it per test by overwriting
# settings.json before every assertion (its readers re-read the file each call).
import json as _json
_SESSION_DATA_DIR = Path(tempfile.mkdtemp(prefix="aurora-qa-"))
os.environ["AURORACODER_DOCKER"] = "0"
os.environ["AURORACODER_DATA_DIR"] = str(_SESSION_DATA_DIR)
_SESSION_DATA_DIR.mkdir(parents=True, exist_ok=True)
(_SESSION_DATA_DIR / "settings.json").write_text(
    _json.dumps({"other": {"memory": {"enabled": True}}}), encoding="utf-8"
)

# Ensure the project root is importable under both laid-out layouts
# (running from AuroraCoder/ root, or from /workspace).
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


@pytest.fixture
def tmp_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated, throw-away workspace directory; also chdir into it.

    Many modules resolve paths against the process CWD or the module-level
    ``WORKSPACE`` constant. Running from a fresh tmp dir keeps each test's
    filesystem effects fully contained and prevents cross-test bleed.
    """
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def env_isolated(monkeypatch: pytest.MonkeyPatch):
    """Snapshot-aware env isolation.

    Anything set/removed via the fixture's ``set``/``delete`` helpers is
    automatically restored at teardown. Use this around any module that reads
    configuration from ``os.environ`` (eg. src.config).
    """
    snapshot = dict(os.environ)

    class _Env:
        def set(self, key: str, value: str) -> None:
            monkeypatch.setenv(key, value)

        def delete(self, key: str) -> None:
            monkeypatch.delenv(key, raising=False)

        def clear_prefix(self, prefix: str) -> None:
            for key in [k for k in os.environ if k.startswith(prefix)]:
                self.delete(key)

    yield _Env()
    # monkeypatch restores env automatically; this is belt-and-braces.
    os.environ.clear()
    os.environ.update(snapshot)


@pytest.fixture
def reload_config():
    """Factory: importlib.reload(src.config) with a post-reload hook.

    ``src.config`` reads environment variables at *import time* and caches the
    results as module globals, so simply ``monkeypatch.setenv`` after the
    first import has no effect. Tests that exercise config behaviour must
    reload the module after mutating the environment.

    Usage::

        def test_default_model(env_isolated, reload_config):
            env_isolated.delete("DEFAULT_MODEL")
            cfg = reload_config()
            assert cfg.DEFAULT_MODEL == "<builtin-fallback>"
    """
    def _reload():
        import src.config as cfg  # noqa: WPS433  local import is intentional
        importlib.reload(cfg)
        return cfg

    # Ensure we reload the original module back at teardown so later tests get
    # a pristine config regardless of what a test set in the environment.
    import src.config as _original  # noqa: WPS433

    yield _reload
    importlib.reload(_original)


class FakeLLMClient:
    """Scriptable chat-completion client injected in place of a real provider.

    ``responses`` is a list (consumed FIFO) whose entries may be:
      * a plain string  -> assistant message content only
      * a dict          -> {"content": str, "tool_calls": [<oai tool_call>]}
      * a callable(obj)->dict  -> invoked with the request, selectable per test
      * an Exception   -> raised (simulates provider failure)

    Call history is captured on ``self.calls`` for assertion.
    """

    def __init__(self, responses: Optional[List[Any]] = None):
        self.responses: List[Any] = list(responses or [])
        self.calls: List[dict] = []

    def chat(self, request: Dict[str, Any]) -> Dict[str, Any]:
        self.calls.append(request)
        if not self.responses:
            raise AssertionError("FakeLLMClient exhausted; no response queued")
        nxt = self.responses.pop(0)
        if isinstance(nxt, BaseException):
            raise nxt
        if callable(nxt):
            return nxt(request)
        if isinstance(nxt, str):
            return {"content": nxt, "tool_calls": []}
        return nxt  # dict already shaped


class FakeCompletedProcess:
    """Stand-in for subprocess.CompletedProcess (returned by FakeSubprocess.run)."""

    def __init__(self, args, stdout="", stderr="", returncode=0):
        self.args = args
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class FakeSubprocess:
    """Hermetic drop-in replacement for ``subprocess.run``.

    Tests register canned outputs keyed by:
      * exact command tuple/list match, or
      * a callable ``predicate(args) -> bool`` matched in registration order.

    Anything unregistered raises ``AssertionError`` so a test that forgets to
    stub a command fails loudly instead of silently exec'ing on the host.
    """

    def __init__(self):
        self._handlers: List[tuple] = []          # (matcher, factory)
        self.calls: List[Any] = []

    def register(
        self,
        *,
        args: Optional[Any] = None,
        predicate: Optional[Callable[[Any], bool]] = None,
        stdout: str = "",
        stderr: str = "",
        returncode: int = 0,
        side_effect: Optional[BaseException] = None,
    ) -> None:
        if args is None and predicate is None:
            raise ValueError("register needs args= or predicate=")

        def _matcher(given):
            if args is not None:
                return list(map(str, given)) == list(map(str, args))
            return predicate(given)

        self._handlers.append((_matcher, (side_effect, stdout, stderr, returncode)))

    def run(self, args, *rest_args, **kwargs):
        self.calls.append(args)
        for matcher, (side_effect, stdout, stderr, returncode) in self._handlers:
            if matcher(args):
                if side_effect is not None:
                    raise side_effect
                return FakeCompletedProcess(args, stdout, stderr, returncode)
        raise AssertionError(f"FakeSubprocess: unhandled command: {args!r}")

    def install(self, monkeypatch: pytest.MonkeyPatch, module=None) -> None:
        """Patch ``subprocess.run`` globally (module-wide) without replacing the
        *whole* ``subprocess`` module object.

        Several modules reference ``subprocess.TimeoutExpired`` /
        ``subprocess.FileNotFoundError`` at exception-handling time, so the
        real module object must remain intact — we only swap ``.run``
        (and the ``CompletedProcess`` type, which tests rarely need).
        """
        monkeypatch.setattr("subprocess.run", self.run, raising=False)
        monkeypatch.setattr("subprocess.CompletedProcess", FakeCompletedProcess, raising=False)


@pytest.fixture
def fake_subprocess():
    """A bare FakeSubprocess instance; tests install it where they need it."""
    return FakeSubprocess()


def pytest_collection_modifyitems(config, items):
    """Auto-skip integration tests unless -m integration is explicitly given."""
    import pytest as _p
    if config.getoption("-m") != "integration":
        skip_it = _p.mark.skip(reason="integration test; run with -m integration")
        for item in items:
            if "integration" in item.keywords and "unit" not in item.keywords:
                item.add_marker(skip_it)