"""
Sanity checks for Memory Layer 2b scaffolding: the Gap Ledger (light,
always on) and the memory-worker dispatcher (heavy, disabled by default).

The dispatcher tests never invoke real docker or touch the filesystem
outside an isolated temp dir -- subprocess/shutil/workspace access are
all monkeypatched. Safe to run alongside a live AuroraCoder container.

Run with (host, conda env with gateway deps):
    python tests/test_memory_layer3.py
"""
import asyncio
import os
import sys
import json
import pathlib
import tempfile
from contextlib import contextmanager

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AURORACODER_DATA_DIR", tempfile.mkdtemp())
# FORCE (not setdefault) — inside a container AURORACODER_DOCKER is already
# exported as "1", so setdefault would no-op and gateway.settings_store would
# bind DATA_DIR to /app/data, ignoring this suite's isolated AURORACODER_DATA_DIR
# and reading the real /app/data/settings.json instead of the temp one below.
os.environ["AURORACODER_DOCKER"] = "0"

# Memory is opt-in (settings.other.memory.enabled defaults to False — see
# memory/settings.py). Gap logging/listing is gated on the master switch
# too, so this suite needs it on; heavy_ops_enabled is left unset/False so
# the dispatcher gate tests below still exercise that specific sub-flag.
# The disabled/no-op path is covered separately in test_memory_toggle.py.
pathlib.Path(os.environ["AURORACODER_DATA_DIR"]).mkdir(parents=True, exist_ok=True)
pathlib.Path(os.environ["AURORACODER_DATA_DIR"], "settings.json").write_text(
    json.dumps({"other": {"memory": {"enabled": True}}}), encoding="utf-8"
)

from memory.gap_store import GapLedger
from memory.ops import dispatcher


def _fresh_ledger() -> GapLedger:
    return GapLedger(storage_dir=pathlib.Path(tempfile.mkdtemp()))


def test_log_gap_creates_open_entry():
    ledger = _fresh_ledger()
    gap = ledger.log_gap("Which ticket tracker does this team use?", scope="project")
    assert gap["status"] == "open"
    assert gap["priority"] == "medium"
    fetched = ledger.get(gap["gap_id"])
    assert fetched is not None and fetched["question"].startswith("Which ticket tracker")


def test_recurring_gap_escalates_instead_of_duplicating():
    ledger = _fresh_ledger()
    g1 = ledger.log_gap("Which ticket tracker does this team use for bugs?", priority="low")
    g2 = ledger.log_gap("What ticket tracker does the team use for bugs?", priority="low")
    assert g2["gap_id"] == g1["gap_id"], "near-duplicate question should escalate, not duplicate"
    assert g2["priority"] in ("medium", "high")
    all_open = ledger.list(status="open")
    assert len(all_open) == 1


def test_unrelated_gaps_do_not_merge():
    ledger = _fresh_ledger()
    g1 = ledger.log_gap("Which ticket tracker does this team use?")
    g2 = ledger.log_gap("What deployment target does this service run on?")
    assert g1["gap_id"] != g2["gap_id"]
    assert len(ledger.list(status="open")) == 2


def test_resolve_and_defer():
    ledger = _fresh_ledger()
    gap = ledger.log_gap("Where are pipeline bugs tracked?")
    assert ledger.resolve(gap["gap_id"], resolved_memory_id="mem_abc123", confidence="high")
    resolved = ledger.get(gap["gap_id"])
    assert resolved["status"] == "resolved" and resolved["resolved_memory_id"] == "mem_abc123"

    gap2 = ledger.log_gap("Something else entirely")
    assert ledger.defer(gap2["gap_id"])
    assert ledger.get(gap2["gap_id"])["status"] == "deferred"

    assert ledger.resolve("gap_doesnotexist", "mem_x") is False
    assert ledger.defer("gap_doesnotexist") is False


def test_invalid_status_rejected():
    ledger = _fresh_ledger()
    gap = ledger.log_gap("q")
    try:
        ledger.set_status(gap["gap_id"], "not-a-real-status")
        assert False, "should have raised"
    except ValueError:
        pass


def test_gateway_gap_routes():
    from fastapi.testclient import TestClient
    from gateway.api import app

    client = TestClient(app)

    r = client.post("/api/memory/gaps", json={"question": "Where do we track incidents?"})
    assert r.status_code == 200, r.text
    gap_id = r.json()["gap"]["gap_id"]

    r = client.get("/api/memory/gaps", params={"status": "open"})
    assert r.status_code == 200
    assert any(g["gap_id"] == gap_id for g in r.json()["gaps"])

    r = client.get(f"/api/memory/gaps/{gap_id}")
    assert r.status_code == 200 and r.json()["gap"]["gap_id"] == gap_id

    r = client.get("/api/memory/gaps/gap_doesnotexist")
    assert r.status_code == 404

    # investigate: heavy_ops disabled by default -> clean no-op, never touches docker
    r = client.post(f"/api/memory/gaps/{gap_id}/investigate")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and "disabled" in body["reason"]

    r = client.post(f"/api/memory/gaps/{gap_id}/defer")
    assert r.status_code == 200
    assert client.get(f"/api/memory/gaps/{gap_id}").json()["gap"]["status"] == "deferred"

    r = client.post(f"/api/memory/gaps/{gap_id}/defer")  # re-defer of an already-deferred gap is idempotent
    assert r.status_code == 200
    r = client.post("/api/memory/gaps/gap_nope/defer")
    assert r.status_code == 404


def test_heavy_ops_disabled_by_default_short_circuits_before_any_io():
    """dispatch_gap_investigation must not touch docker/filesystem at all
    when the settings gate is off -- this is the most important safety
    property of the whole dispatcher."""
    orig_snapshot, orig_spawn = dispatcher.snapshot_workspace, dispatcher.spawn_worker
    calls = []
    dispatcher.snapshot_workspace = lambda gap_id: calls.append("snapshot") or pathlib.Path(tempfile.mkdtemp())
    dispatcher.spawn_worker = lambda gap_id, snap: calls.append("spawn") or "fake-container"
    try:
        from memory.gap_store import get_gap_ledger
        ledger = get_gap_ledger()
        gap = ledger.log_gap("test gap for heavy-ops gate")

        result = dispatcher.dispatch_gap_investigation(gap["gap_id"])
        assert result["ok"] is False
        assert "disabled" in result["reason"]
        assert calls == [], "must not snapshot or spawn anything while disabled"
    finally:
        dispatcher.snapshot_workspace = orig_snapshot
        dispatcher.spawn_worker = orig_spawn


def test_docker_run_args_construction_uses_expected_conventions():
    args = dispatcher.build_docker_run_args("gap_test123", pathlib.Path("/tmp/snap"))
    assert "run" in args and "-d" in args
    assert "--name" in args
    name_idx = args.index("--name")
    assert args[name_idx + 1] == "auroracoder-memory-worker-gap_test123"
    assert "AURORACODER_ROLE=memory-worker" in args
    assert any(a.endswith(":/workspace") for a in args)
    assert args[-1] == dispatcher.DEFAULT_WORKER_IMAGE
    # No host port publishing — the worker is reached over the shared
    # user-defined network by container name (see module docstring
    # "Reaching the worker"), never via localhost/docker port discovery.
    assert "-p" not in args
    assert "--network" in args
    network_idx = args.index("--network")
    assert args[network_idx + 1] == dispatcher.MEMORY_NETWORK_NAME


def test_worker_base_url_uses_container_dns_name():
    url = dispatcher.worker_base_url("gap_test123")
    assert url == "http://auroracoder-memory-worker-gap_test123:8080"


def test_own_container_name_reads_env_with_fallback():
    orig = os.environ.pop("AURORACODER_CONTAINER_NAME", None)
    try:
        assert dispatcher._own_container_name() == dispatcher.DEFAULT_CONTAINER_NAME
        os.environ["AURORACODER_CONTAINER_NAME"] = "some-custom-name"
        assert dispatcher._own_container_name() == "some-custom-name"
    finally:
        if orig is None:
            os.environ.pop("AURORACODER_CONTAINER_NAME", None)
        else:
            os.environ["AURORACODER_CONTAINER_NAME"] = orig


def test_ensure_memory_network_tolerates_already_exists():
    """Idempotent by design — a network that already exists (or a
    container that's already connected) is success, not failure, since
    both calls are re-run on every spawn (see ensure_memory_network doc)."""
    orig_run_docker = dispatcher._run_docker
    calls = []

    class _FakeResult:
        def __init__(self, stderr):
            self.returncode = 1
            self.stderr = stderr

    def fake_run_docker(args):
        calls.append(args)
        if args[:2] == ["network", "create"]:
            return _FakeResult(f"Error: network with name {dispatcher.MEMORY_NETWORK_NAME} already exists")
        return _FakeResult("Error: endpoint already connected")

    dispatcher._run_docker = fake_run_docker
    try:
        assert dispatcher.ensure_memory_network() is True
        assert len(calls) == 2
    finally:
        dispatcher._run_docker = orig_run_docker


def test_ensure_memory_network_reports_real_failures():
    orig_run_docker = dispatcher._run_docker

    class _FakeResult:
        returncode = 1
        stderr = "docker: command not found"

    dispatcher._run_docker = lambda args: _FakeResult()
    try:
        assert dispatcher.ensure_memory_network() is False
    finally:
        dispatcher._run_docker = orig_run_docker


def test_spawn_worker_handles_docker_failure_gracefully():
    orig_run_docker = dispatcher._run_docker

    class _FakeResult:
        returncode = 1
        stderr = "docker: command not found"

    dispatcher._run_docker = lambda args: _FakeResult()
    try:
        result = dispatcher.spawn_worker("gap_x", pathlib.Path("/tmp/snap"))
        assert result is None
    finally:
        dispatcher._run_docker = orig_run_docker


def test_spawn_worker_skips_run_when_network_setup_fails():
    """spawn_worker must not even attempt `docker run` for the worker
    itself if ensure_memory_network() failed — there'd be no way to
    reach it anyway."""
    orig_ensure = dispatcher.ensure_memory_network
    orig_run_docker = dispatcher._run_docker
    calls = []
    dispatcher.ensure_memory_network = lambda: False
    dispatcher._run_docker = lambda args: calls.append(args)
    try:
        result = dispatcher.spawn_worker("gap_y", pathlib.Path("/tmp/snap"))
        assert result is None
        assert calls == [], "must not call docker run at all when network setup failed"
    finally:
        dispatcher.ensure_memory_network = orig_ensure
        dispatcher._run_docker = orig_run_docker


# ---------------------------------------------------------------------------
# The investigation protocol itself: driving an already-spawned, already-
# ready worker through /api/chat, and the end-to-end dispatch flow. All
# HTTP is mocked -- these tests never make a real network call.
# ---------------------------------------------------------------------------

@contextmanager
def _patched(module, **attrs):
    """Patch *attrs* onto *module*, restoring the originals on exit --
    used below to avoid repeating the same manual save/restore
    boilerplate the earlier tests in this file use, now that several
    tests need to patch many attributes (possibly across two modules) at
    once."""
    originals = {name: getattr(module, name) for name in attrs}
    for name, value in attrs.items():
        setattr(module, name, value)
    try:
        yield
    finally:
        for name, value in originals.items():
            setattr(module, name, value)


class _FakeSSEResponse:
    def __init__(self, status_code=200, lines=None, text=""):
        self.status_code = status_code
        self._lines = lines or []
        self.text = text

    def iter_lines(self, decode_unicode=True):
        return iter(self._lines)

    def close(self):
        pass


def _sse_line(payload: dict) -> str:
    return f"data: {json.dumps(payload)}"


def test_wait_for_worker_ready_succeeds_once_health_responds():
    class _FakeHealthResp:
        status_code = 200

    with _patched(dispatcher.requests, get=lambda url, timeout=3: _FakeHealthResp()):
        assert dispatcher._wait_for_worker_ready("http://fake-worker:8080", timeout=5) is True


def test_wait_for_worker_ready_times_out_when_unreachable():
    def _raise(*a, **kw):
        raise requests.exceptions.ConnectionError("connection refused")

    with _patched(dispatcher.requests, get=_raise), _patched(dispatcher.time, sleep=lambda s: None):
        assert dispatcher._wait_for_worker_ready("http://fake-worker:8080", timeout=0.01) is False


def test_investigate_gap_via_worker_returns_transcript_on_success():
    final_messages = [{"role": "assistant", "tool_calls": [
        {"function": {"name": "report_findings", "arguments": json.dumps({"resolved": True})}}
    ]}]
    lines = [
        _sse_line({"status": "running", "raw_messages": []}),
        _sse_line({"status": "completed", "raw_messages": final_messages}),
    ]
    with _patched(dispatcher.requests, post=lambda *a, **kw: _FakeSSEResponse(200, lines)):
        result = dispatcher.investigate_gap_via_worker("http://fake-worker:8080", "some question")
    assert result == final_messages


def test_investigate_gap_via_worker_returns_none_on_non_200():
    with _patched(dispatcher.requests, post=lambda *a, **kw: _FakeSSEResponse(500, [], text="boom")):
        result = dispatcher.investigate_gap_via_worker("http://fake-worker:8080", "q")
    assert result is None


def test_investigate_gap_via_worker_returns_none_on_connection_error():
    def _raise(*a, **kw):
        raise requests.exceptions.ConnectionError("nope")

    with _patched(dispatcher.requests, post=_raise):
        result = dispatcher.investigate_gap_via_worker("http://fake-worker:8080", "q")
    assert result is None


def test_investigate_gap_via_worker_salvages_transcript_on_mid_stream_error():
    """A worker that errors out mid-stream (e.g. hit a provider hiccup)
    should still hand back whatever transcript we saw before that -- the
    investigator may have already called report_findings on an earlier
    turn."""
    partial_messages = [{"role": "assistant", "tool_calls": [
        {"function": {"name": "report_findings", "arguments": json.dumps({"resolved": True})}}
    ]}]
    lines = [_sse_line({"status": "running", "raw_messages": partial_messages})]

    class _DiesPartway(_FakeSSEResponse):
        def iter_lines(self, decode_unicode=True):
            yield from self._lines
            raise requests.exceptions.ChunkedEncodingError("connection dropped")

    with _patched(dispatcher.requests, post=lambda *a, **kw: _DiesPartway(200, lines)):
        result = dispatcher.investigate_gap_via_worker("http://fake-worker:8080", "q")
    assert result == partial_messages


def _fresh_singleton_ledger():
    """dispatch_gap_investigation always goes through the process-wide
    get_gap_ledger() singleton (unlike the standalone-ledger tests above),
    so these flow tests use it too rather than a throwaway instance."""
    from memory.gap_store import get_gap_ledger
    return get_gap_ledger()


def test_dispatch_gap_investigation_full_success_path():
    """The whole pipeline, everything mocked: spawn -> wait-ready ->
    investigate -> judged write pass -> ledger.resolve -> teardown."""
    calls = []
    import memory.ops.extractor as extractor_module

    with _patched(
        dispatcher,
        heavy_ops_enabled=lambda: True,
        snapshot_workspace=lambda gap_id: (calls.append("snapshot") or pathlib.Path(tempfile.mkdtemp())),
        spawn_worker=lambda gap_id, snap: (calls.append("spawn") or f"auroracoder-memory-worker-{gap_id}"),
        teardown_worker=lambda name: calls.append(("teardown", name)),
        cleanup_snapshot=lambda gap_id: calls.append("cleanup"),
        _wait_for_worker_ready=lambda base_url, timeout=30: calls.append("ready") or True,
        investigate_gap_via_worker=lambda base_url, question, max_iterations=20: (
            calls.append("investigate") or [{"role": "assistant", "tool_calls": []}]
        ),
    ), _patched(
        extractor_module,
        run_gap_investigation_extraction=lambda gap_id, msgs: (calls.append("extract") or ("mem_fake123", "high")),
    ):
        ledger = _fresh_singleton_ledger()
        gap = ledger.log_gap("Where are pipeline bugs tracked (full-flow test)?")

        result = dispatcher.dispatch_gap_investigation(gap["gap_id"])

        assert result == {"ok": True, "gap_id": gap["gap_id"], "memory_id": "mem_fake123", "confidence": "high"}
        resolved = ledger.get(gap["gap_id"])
        assert resolved["status"] == "resolved" and resolved["resolved_memory_id"] == "mem_fake123"
        assert calls == ["snapshot", "spawn", "ready", "investigate", "extract", ("teardown", f"auroracoder-memory-worker-{gap['gap_id']}"), "cleanup"]


def test_dispatch_gap_investigation_defers_when_worker_never_becomes_ready():
    with _patched(
        dispatcher,
        heavy_ops_enabled=lambda: True,
        snapshot_workspace=lambda gap_id: pathlib.Path(tempfile.mkdtemp()),
        spawn_worker=lambda gap_id, snap: f"auroracoder-memory-worker-{gap_id}",
        teardown_worker=lambda name: None,
        cleanup_snapshot=lambda gap_id: None,
        _wait_for_worker_ready=lambda base_url, timeout=30: False,
    ):
        ledger = _fresh_singleton_ledger()
        gap = ledger.log_gap("A gap whose worker never comes up")
        result = dispatcher.dispatch_gap_investigation(gap["gap_id"])
        assert result["ok"] is False and "ready" in result["reason"]
        assert ledger.get(gap["gap_id"])["status"] == "deferred"


def test_dispatch_gap_investigation_defers_when_investigation_yields_no_transcript():
    with _patched(
        dispatcher,
        heavy_ops_enabled=lambda: True,
        snapshot_workspace=lambda gap_id: pathlib.Path(tempfile.mkdtemp()),
        spawn_worker=lambda gap_id, snap: f"auroracoder-memory-worker-{gap_id}",
        teardown_worker=lambda name: None,
        cleanup_snapshot=lambda gap_id: None,
        _wait_for_worker_ready=lambda base_url, timeout=30: True,
        investigate_gap_via_worker=lambda base_url, question, max_iterations=20: None,
    ):
        ledger = _fresh_singleton_ledger()
        gap = ledger.log_gap("A gap whose HTTP exchange fails outright")
        result = dispatcher.dispatch_gap_investigation(gap["gap_id"])
        assert result["ok"] is False and "transcript" in result["reason"]
        assert ledger.get(gap["gap_id"])["status"] == "deferred"


def test_dispatch_gap_investigation_defers_when_finding_is_rejected_by_write_pass():
    """Mirrors the normal path's no-op default: the investigator reporting
    (or the judge rejecting) is not a bug to route around -- it's the
    fail-open, prefer-silence default working as intended."""
    import memory.ops.extractor as extractor_module

    with _patched(
        dispatcher,
        heavy_ops_enabled=lambda: True,
        snapshot_workspace=lambda gap_id: pathlib.Path(tempfile.mkdtemp()),
        spawn_worker=lambda gap_id, snap: f"auroracoder-memory-worker-{gap_id}",
        teardown_worker=lambda name: None,
        cleanup_snapshot=lambda gap_id: None,
        _wait_for_worker_ready=lambda base_url, timeout=30: True,
        investigate_gap_via_worker=lambda base_url, question, max_iterations=20: [
            {"role": "assistant", "tool_calls": [
                {"function": {"name": "report_findings", "arguments": json.dumps({"resolved": False, "notes": "no evidence"})}}
            ]}
        ],
    ), _patched(extractor_module, run_gap_investigation_extraction=lambda gap_id, msgs: None):
        ledger = _fresh_singleton_ledger()
        gap = ledger.log_gap("A gap the investigator honestly could not resolve")
        result = dispatcher.dispatch_gap_investigation(gap["gap_id"])
        assert result["ok"] is False and "judgment" in result["reason"]
        assert ledger.get(gap["gap_id"])["status"] == "deferred"


def test_gap_investigation_tool_mode_is_minimal_and_ends_with_report_findings():
    """The one-shot HTTP call the dispatcher makes uses tools='gap_investigation'
    (see get_filtered_tools in src/web_api/app.py). Gap investigation merged
    into the unified memory-maintenance worker, so that mode now resolves to
    exactly the shared unified set (tool_definitions.py: GAP_INVESTIGATION_TOOLS
    ≡ MEMORY_MAINTENANCE_TOOLS): remember/forget + the read tools. Still never
    recall/log_gap/subagent/write_file/edit_file/delete_file."""
    from src.web_api.app import get_filtered_tools
    from src.tool_definitions import GAP_INVESTIGATION_TOOLS

    names = {td["function"]["name"] for td in get_filtered_tools("gap_investigation")}
    assert names == GAP_INVESTIGATION_TOOLS
    assert "report_findings" in names
    assert "remember" in names and "forget" in names
    assert not names & {"recall", "log_gap", "subagent", "write_file", "edit_file", "delete_file"}


def test_build_docker_run_args_forwards_configured_provider_keys_only():
    """Regression test for a real bug caught by manual Docker validation: a
    freshly-spawned worker has no settings.json of its own, so unless the
    main container's own already-configured provider keys are forwarded as
    -e values, the worker can never reach any LLM and every investigation
    dead-ends on 'not configured'. Only keys actually set get forwarded;
    unset ones are silently skipped rather than passed through empty."""
    originals = {name: os.environ.get(name) for name in dispatcher.PROVIDER_API_KEY_ENV_VARS}
    try:
        os.environ["DEEPSEEK_API_KEY"] = "sk-fake-deepseek"
        os.environ.pop("OPENCODE_API_KEY", None)
        os.environ.pop("NVIDIA_API_KEY", None)
        args = dispatcher.build_docker_run_args("gap_xyz", pathlib.Path("/tmp/empty"))
    finally:
        for name, value in originals.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    assert "-e" in args and "DEEPSEEK_API_KEY=sk-fake-deepseek" in args
    assert not any(a.startswith("OPENCODE_API_KEY=") for a in args)
    assert not any(a.startswith("NVIDIA_API_KEY=") for a in args)


def test_get_memory_extraction_config_resolves_a_real_model_under_the_provider_family_scheme():
    """Regression test for a real bug found merging in a `dev` refactor that
    replaced per-variant provider ids (e.g. 'opencode-ds-v4-pro' WAS a
    provider id) with provider *families* (only 'deepseek'/'opencode'/
    'nvidia' are provider ids now; models are a separate, per-family list).
    get_memory_extraction_config() used to call a get_default_provider()
    helper that no longer exists post-refactor (would have been a hard
    NameError on every call with no explicit extraction_provider set -- the
    common case), and even when a provider_id did resolve, resolve_provider()
    no longer guarantees a non-empty 'model' the way per-variant ids used
    to. Exercises the REAL resolution chain (settings -> resolve_provider ->
    PROVIDER_DEFAULT_MODELS), unlike the extraction-pipeline tests in
    test_memory_layer2.py which mock this function out entirely."""
    from gateway.provider_registry import get_memory_extraction_config
    from src.config import DEFAULT_PROVIDER, PROVIDER_DEFAULT_MODELS

    data_dir = pathlib.Path(os.environ["AURORACODER_DATA_DIR"])
    settings_path = data_dir / "settings.json"
    original = settings_path.read_text(encoding="utf-8")
    # The settings.json we write here has no provider keys, so resolve_provider()
    # should resolve api_key to "". Two sources can otherwise leak a real key:
    #   (a) get_api_key() falls back to <PROVIDER>_API_KEY env vars, and this
    #       host/container exports DEEPSEEK/OPENCODE/NVIDIA_API_KEY;
    #   (b) src/config.py captures those same env vars INTO MODEL_PROVIDERS at
    #       IMPORT time, and resolve_provider() keeps the baked-in key when
    #       get_api_key() returns "" (its `elif not prov["api_key"]` branch).
    # So clearing the env at test time alone is too late — config.py already
    # grabbed them at import. Clear BOTH for this test's duration, restore below.
    _provider_ids = ("deepseek", "opencode", "nvidia")
    _provider_key_env = tuple(f"{pid.upper()}_API_KEY" for pid in _provider_ids)
    _saved_env = {name: os.environ.get(name) for name in _provider_key_env}
    for _name in _provider_key_env:
        os.environ.pop(_name, None)
    from src.config import MODEL_PROVIDERS as _MP
    _saved_mp_keys = {pid: _MP[pid].get("api_key") for pid in _provider_ids if pid in _MP}
    for _pid, _val in _saved_mp_keys.items():
        _MP[_pid]["api_key"] = ""
    try:
        # No extraction_provider, no agent default_model -- must still
        # resolve to *some* real, non-empty model (not crash, not "").
        settings_path.write_text(json.dumps({"other": {"memory": {"enabled": True}}}), encoding="utf-8")
        cfg = get_memory_extraction_config()
        assert cfg["provider_id"] == DEFAULT_PROVIDER
        assert cfg["model"] == PROVIDER_DEFAULT_MODELS[DEFAULT_PROVIDER][0]["id"]

        # Falls through to the agent's own structured default_model when
        # extraction_provider is unset -- same {"provider", "model"} shape
        # used everywhere else post-refactor (dev's "structured {provider,
        # model}" change), not a legacy "provider::model" string.
        settings_path.write_text(json.dumps({"other": {
            "memory": {"enabled": True},
            "agent": {"default_model": {"provider": "opencode", "model": "deepseek-v4-flash"}},
        }}), encoding="utf-8")
        cfg = get_memory_extraction_config()
        assert cfg == {
            "provider_id": "opencode",
            "base_url": "https://opencode.ai/zen/go/v1",
            "api_key": "",
            "model": "deepseek-v4-flash",
        }

        # An explicit extraction_provider must be a provider *family* id
        # (never a legacy per-variant id) and always resolves to that
        # family's default model.
        settings_path.write_text(json.dumps({"other": {
            "memory": {"enabled": True, "extraction_provider": "nvidia"},
        }}), encoding="utf-8")
        cfg = get_memory_extraction_config()
        assert cfg["provider_id"] == "nvidia"
        assert cfg["model"] == PROVIDER_DEFAULT_MODELS["nvidia"][0]["id"]
    finally:
        settings_path.write_text(original, encoding="utf-8")
        for _name, _val in _saved_env.items():
            if _val is None:
                os.environ.pop(_name, None)
            else:
                os.environ[_name] = _val
        for _pid, _val in _saved_mp_keys.items():
            _MP[_pid]["api_key"] = (_val if _val is not None else "")


def test_gap_investigation_tool_mode_survives_worker_own_memory_being_disabled():
    """Regression test for a real bug caught by manual Docker validation: a
    freshly-spawned memory-worker container never shares the main
    container's settings.json (deliberate isolation -- see
    dispatcher.py's module docstring), so its OWN memory_enabled() reads
    back False by default no matter what the dispatching side decided.
    get_filtered_tools("gap_investigation") must not let the master-switch
    filter (memory_filter_tools) strip report_findings in that case --
    the allowlist for this mode is already fixed and deliberate, decided
    by the caller before the worker was even spawned."""
    import src.tool_definitions as tool_definitions
    from src.tool_definitions import GAP_INVESTIGATION_TOOLS
    from src.web_api.app import get_filtered_tools

    with _patched(tool_definitions, memory_enabled=lambda: False):
        names = {td["function"]["name"] for td in get_filtered_tools("gap_investigation")}
    assert "report_findings" in names, "master switch filter leaked into gap_investigation mode and stripped report_findings"
    assert names == GAP_INVESTIGATION_TOOLS


# ---------------------------------------------------------------------------
# Phase 1: memory-maintenance dispatch (extraction / consolidation through a
# real AuroraCoder worker, persistent transcript). The worker mounts the main
# container's memory data dir read-write, so its remember/forget calls write
# STRAIGHT into the shared store — there are no emit tools, no plan extraction,
# and no dispatcher-side application anymore; the persisted transcript IS the
# trace. All docker/HTTP/filesystem operations are mocked -- these tests never
# touch a real container or socket.
# ---------------------------------------------------------------------------


def test_memory_maintenance_tool_mode_is_unified_set():
    """get_filtered_tools('memory_maintenance') must resolve to EXACTLY the
    unified worker set: remember/forget (direct writes to the shared store)
    plus the read tools. No emit tools, no recall/log_gap/subagent/write."""
    from src.web_api.app import get_filtered_tools
    from src.tool_definitions import MEMORY_MAINTENANCE_TOOLS
    names = {td["function"]["name"] for td in get_filtered_tools("memory_maintenance")}
    assert names == MEMORY_MAINTENANCE_TOOLS
    assert names == {"remember", "forget", "read_file", "list_directory", "run_terminal_command", "report_findings"}
    assert not names & {"recall", "log_gap", "subagent", "write_file", "edit_file",
                        "delete_file", "emit_memory_plan", "emit_consolidation_plan"}


def test_gap_investigation_mode_same_as_maintenance():
    """Gap investigation is just one maintenance kind now — both modes share
    the same unified tool set (GAP_INVESTIGATION_TOOLS ≡ MEMORY_MAINTENANCE_TOOLS)."""
    from src.web_api.app import get_filtered_tools
    gap_names = {td["function"]["name"] for td in get_filtered_tools("gap_investigation")}
    maint_names = {td["function"]["name"] for td in get_filtered_tools("memory_maintenance")}
    assert gap_names == maint_names


def test_remember_in_memory_maintenance_mode():
    from src.web_api.app import get_filtered_tools
    names = {td["function"]["name"] for td in get_filtered_tools("memory_maintenance")}
    assert "remember" in names


def test_forget_in_memory_maintenance_mode():
    from src.web_api.app import get_filtered_tools
    names = {td["function"]["name"] for td in get_filtered_tools("memory_maintenance")}
    assert "forget" in names


def test_memory_maintenance_mode_skips_master_switch():
    """Same isolation contract as gap_investigation: the worker has no
    settings.json, so its own memory_enabled() reads False. memory_maintenance
    mode must skip the master-switch filter (memory_filter_tools) exactly like
    gap_investigation mode does, or remember/forget would be silently stripped
    and every maintenance task would dead-end."""
    import src.tool_definitions as tool_definitions
    from src.tool_definitions import MEMORY_MAINTENANCE_TOOLS
    from src.web_api.app import get_filtered_tools
    with _patched(tool_definitions, memory_enabled=lambda: False):
        names = {td["function"]["name"] for td in get_filtered_tools("memory_maintenance")}
    assert names == MEMORY_MAINTENANCE_TOOLS
    assert "remember" in names and "forget" in names


def test_dispatch_memory_maintenance_consolidation_persists_transcript():
    """The worker's remember/forget calls write directly into the shared store
    (mounted data dir) — the dispatcher no longer extracts or applies a plan.
    What it must do is persist the full transcript (request + remember/forget
    tool calls) into the conversation store so the trace is observable."""
    from gateway.conversation_store import store

    raw_messages = [{"role": "assistant", "tool_calls": [
        {"function": {"name": "forget", "arguments": json.dumps({"memory_id": "loser-1"})}},
        {"function": {"name": "remember", "arguments": json.dumps({
            "memory_id": "keeper-1", "content": "merged content", "description": "merged",
            "plane": "world", "type": "reference", "scope": "project", "confidence": "medium",
        })}},
    ]}]

    calls = []
    with _patched(
        dispatcher,
        heavy_ops_enabled=lambda: True,
        snapshot_workspace=lambda maint_id: (calls.append("snapshot") or pathlib.Path(tempfile.mkdtemp())),
        spawn_worker=lambda maint_id, snap: (calls.append("spawn") or f"worker-{maint_id}"),
        teardown_worker=lambda name: calls.append(("teardown", name)),
        cleanup_snapshot=lambda maint_id: calls.append("cleanup"),
        _wait_for_worker_ready=lambda base_url, timeout=30: calls.append("ready") or True,
        _run_maintenance_via_worker=lambda base_url, kind, payload, max_iterations=15: (
            calls.append("run-maintenance") or raw_messages
        ),
    ):
        result = dispatcher.dispatch_memory_maintenance("consolidation", {
            "corpus": ["fake-corpus"], "now_iso": "2026-01-01T00:00:00",
        })

    assert result["ok"] is True
    assert result["kind"] == "consolidation"
    assert "conversation_id" in result and "container" in result
    # No plan extraction/application anymore — the worker wrote to the shared
    # store itself, so the result carries no plan/applied keys.
    assert "plan" not in result and "applied" not in result
    assert "merged" not in result and "deleted" not in result and "written" not in result

    # The transcript is persisted with the remember/forget calls visible.
    conv = store.get_messages(result["conversation_id"])
    assert conv is not None and len(conv) > 0
    conv_str = json.dumps(conv)
    assert '"name": "remember"' in conv_str and '"name": "forget"' in conv_str

    # Lifecycle order (teardown runs in finally before the assertion).
    assert calls[0] == "snapshot"
    assert calls[1] == "spawn"
    assert calls[2] == "ready"
    assert calls[3] == "run-maintenance"
    assert calls[-1] == "cleanup"
    assert "teardown" in [c[0] if isinstance(c, tuple) else c for c in calls]


def test_dispatch_memory_maintenance_extraction_persists_transcript():
    """End-to-end extraction maintenance under the direct-write model: the
    worker calls remember itself; the dispatcher persists the trace and does
    not apply any plan."""
    from gateway.conversation_store import store

    raw_messages = [{"role": "assistant", "tool_calls": [
        {"function": {"name": "remember", "arguments": json.dumps({
            "plane": "world", "type": "reference", "scope": "project",
            "content": "Extraction via worker succeeded.",
            "description": "worker extraction test", "confidence": "high",
        })}},
    ]}]

    with _patched(
        dispatcher,
        heavy_ops_enabled=lambda: True,
        snapshot_workspace=lambda maint_id: pathlib.Path(tempfile.mkdtemp()),
        spawn_worker=lambda maint_id, snap: f"worker-{maint_id}",
        teardown_worker=lambda name: None,
        cleanup_snapshot=lambda maint_id: None,
        _wait_for_worker_ready=lambda base_url, timeout=30: True,
        _run_maintenance_via_worker=lambda base_url, kind, payload, max_iterations=15: raw_messages,
    ):
        result = dispatcher.dispatch_memory_maintenance("extraction", {
            "transcript": "USER: Test.\nASSISTANT: Got it.", "now_iso": "2026-01-01T00:00:00",
        })

    assert result["ok"] is True
    assert result["kind"] == "extraction"
    assert "conversation_id" in result and "container" in result
    assert "plan" not in result and "written" not in result

    conv = store.get_messages(result["conversation_id"])
    assert conv is not None and len(conv) > 0
    # The first message is the title surrogate; the remember call is visible.
    assert conv[0]["content"].startswith("Memory maintenance: extraction")
    assert '"name": "remember"' in json.dumps(conv)


def test_dispatch_memory_maintenance_gated_when_disabled():
    """Both memory_enabled AND heavy_ops_enabled must be True; either gate
    off → dispatch_memory_maintenance short-circuits before any IO."""
    calls = []
    # Case 1: heavy_ops is OFF (the default — no explicit heavy_ops_enabled in
    # this suite's settings.json). No need to patch heavy_ops_enabled at all;
    # settings reads False and that's the gate we're testing.
    with _patched(
        dispatcher,
        snapshot_workspace=lambda maint_id: calls.append("snapshot") or pathlib.Path(tempfile.mkdtemp()),
        spawn_worker=lambda maint_id, snap: calls.append("spawn"),
    ):
        r = dispatcher.dispatch_memory_maintenance("consolidation")
        assert r["ok"] is False and "heavy_ops" in r["reason"]
        assert calls == []

    # Case 2: memory is OFF, heavy_ops is ON. The memory gate fires first.
    with _patched(
        dispatcher,
        memory_enabled=lambda: False,
        heavy_ops_enabled=lambda: True,
        snapshot_workspace=lambda maint_id: calls.append("snapshot"),
    ):
        r = dispatcher.dispatch_memory_maintenance("consolidation")
        assert r["ok"] is False and "memory" in r["reason"]
        assert calls == []


def test_dispatch_memory_maintenance_spawn_failure_persists_failed_trace():
    """Even when the worker never comes up, the intent + request must be
    persisted so the user can see what was attempted."""
    from gateway.conversation_store import store

    with _patched(
        dispatcher,
        heavy_ops_enabled=lambda: True,
        snapshot_workspace=lambda maint_id: pathlib.Path(tempfile.mkdtemp()),
        spawn_worker=lambda maint_id, snap: None,  # docker failure
        teardown_worker=lambda name: None,
        cleanup_snapshot=lambda maint_id: None,
    ):
        result = dispatcher.dispatch_memory_maintenance("consolidation", {
            "corpus": ["test-corpus"], "now_iso": "2026-01-01",
        })

    assert result["ok"] is False
    assert "spawn" in result["reason"]
    conv_id = result.get("conversation_id")
    assert conv_id and conv_id.startswith("memory-maintenance:consolidation:")
    conv = store.get_messages(conv_id)
    assert conv is not None and any("test-corpus" in str(m.get("content", "")) for m in conv)


def test_dispatch_memory_maintenance_teardown_even_on_error():
    """If run_maintenance raises (simulated by an exception in
    _run_maintenance_via_worker), teardown + cleanup must still happen."""
    calls = []
    def _failing_run(*a, **kw):
        calls.append("run")
        raise RuntimeError("worker crashed mid-transcript")

    with _patched(
        dispatcher,
        heavy_ops_enabled=lambda: True,
        snapshot_workspace=lambda maint_id: (calls.append("snapshot") or pathlib.Path(tempfile.mkdtemp())),
        spawn_worker=lambda maint_id, snap: (calls.append("spawn") or f"worker-{maint_id}"),
        teardown_worker=lambda name: calls.append(("teardown", name)),
        cleanup_snapshot=lambda maint_id: calls.append("cleanup"),
        _wait_for_worker_ready=lambda base_url, timeout=30: calls.append("ready") or True,
        _run_maintenance_via_worker=_failing_run,
    ):
        result = dispatcher.dispatch_memory_maintenance("extraction", {"transcript": "test"})

    assert result["ok"] is False
    assert "teardown" in [c[0] if isinstance(c, tuple) else c for c in calls]
    assert "cleanup" in calls


# ---------------------------------------------------------------------------
# The periodic gap scheduler (memory/ops/gap_scheduler.py) -- the automatic
# trigger path alongside the manual /investigate route. Always uses its own
# throwaway ledger (via get_gap_ledger monkeypatched onto the module), never
# the process-wide singleton, so these are fully isolated from the dispatch-
# flow tests above and from each other regardless of run order.
# ---------------------------------------------------------------------------

def _drain_pending_tasks():
    """Await every task the code under test fired with asyncio.create_task
    (sweep_once dispatches are fire-and-forget) before a test's assertions
    run, and before the event loop closes -- otherwise asyncio.run() would
    just cancel them silently."""
    async def _drain():
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if pending:
            await asyncio.gather(*pending)
    return _drain()


def test_gap_sweep_settings_defaults_and_gating():
    from memory import settings as memory_settings

    data_dir = pathlib.Path(os.environ["AURORACODER_DATA_DIR"])
    settings_path = data_dir / "settings.json"
    original = settings_path.read_text(encoding="utf-8")
    try:
        settings_path.write_text(
            json.dumps({"other": {"memory": {"enabled": True}}}), encoding="utf-8"
        )
        assert memory_settings.gap_sweep_interval_hours() == 24
        assert memory_settings.gap_sweep_max_concurrent() == 1
        assert memory_settings.gap_sweep_batch_size() == 1
        # heavy_ops_enabled is off in this settings.json -> gap_auto_sweep_enabled
        # must be False too regardless of its own default, same "master gates
        # sub-flag" pattern as passive_extraction_enabled/heavy_ops_enabled.
        assert memory_settings.gap_auto_sweep_enabled() is False

        settings_path.write_text(
            json.dumps({"other": {"memory": {
                "enabled": True, "heavy_ops_enabled": True,
                "gap_sweep_interval_hours": 6, "gap_sweep_max_concurrent": 3, "gap_sweep_batch_size": 2,
            }}}), encoding="utf-8",
        )
        assert memory_settings.gap_auto_sweep_enabled() is True  # default True once heavy_ops is on
        assert memory_settings.gap_sweep_interval_hours() == 6
        assert memory_settings.gap_sweep_max_concurrent() == 3
        assert memory_settings.gap_sweep_batch_size() == 2

        settings_path.write_text(
            json.dumps({"other": {"memory": {
                "enabled": True, "heavy_ops_enabled": True, "gap_auto_sweep_enabled": False,
            }}}), encoding="utf-8",
        )
        assert memory_settings.gap_auto_sweep_enabled() is False, (
            "explicit opt-out of the automatic sweep must hold even with heavy_ops on"
        )
    finally:
        settings_path.write_text(original, encoding="utf-8")


def test_recover_stale_investigating_gaps_resets_to_open():
    from memory.ops import gap_scheduler

    ledger = _fresh_ledger()
    stuck = ledger.log_gap("A gap stuck mid-investigation from a crashed process")
    ledger.set_status(stuck["gap_id"], "investigating")
    # Deliberately unrelated wording (no shared words with each other or with
    # `stuck` above) so GapLedger.log_gap's recurring-gap dedup never merges
    # any of these three rows together.
    already_open = ledger.log_gap("Which payment gateway integration handles refunds?")
    resolved = ledger.log_gap("Does the CI pipeline run on GitHub Actions or Jenkins?")
    ledger.resolve(resolved["gap_id"], "mem_unrelated")

    with _patched(gap_scheduler, get_gap_ledger=lambda: ledger):
        recovered = gap_scheduler.recover_stale_investigating_gaps()

    assert recovered == 1
    assert ledger.get(stuck["gap_id"])["status"] == "open"
    assert ledger.get(already_open["gap_id"])["status"] == "open"
    assert ledger.get(resolved["gap_id"])["status"] == "resolved", "must not touch non-investigating gaps"


def test_sweep_once_dispatches_only_priority_high_open_gaps():
    from memory.ops import gap_scheduler
    import memory.ops.dispatcher as dispatcher_module

    # Deliberately unrelated wording across all three -- GapLedger.log_gap's
    # recurring-gap dedup operates on keyword overlap, so anything sharing
    # too many words (even just "priority gap") could silently collapse
    # these into one row instead of three, which would defeat the point of
    # this test.
    ledger = _fresh_ledger()
    high_gap = ledger.log_gap("Which message queue broker does the order service use?", priority="high")
    ledger.log_gap("Where is the staging environment's database hosted?", priority="medium")
    ledger.log_gap("Who owns the mobile app's release process?", priority="low")

    dispatched = []

    def fake_dispatch(gap_id):
        dispatched.append(gap_id)
        return {"ok": True, "gap_id": gap_id}

    async def _run():
        with _patched(
            gap_scheduler,
            get_gap_ledger=lambda: ledger,
            gap_auto_sweep_enabled=lambda: True,
            gap_sweep_batch_size=lambda: 5,
            gap_sweep_max_concurrent=lambda: 5,
        ), _patched(dispatcher_module, dispatch_gap_investigation=fake_dispatch):
            summary = await gap_scheduler.sweep_once()
            await _drain_pending_tasks()
        return summary

    summary = asyncio.run(_run())
    assert summary == {"eligible": 1, "dispatched": 1, "skipped": 0}
    assert dispatched == [high_gap["gap_id"]], "medium/low priority gaps must never be auto-dispatched"
    assert gap_scheduler._running_gap_ids == set(), "must always release the gap id once its dispatch completes"


def test_sweep_once_respects_batch_size_and_concurrency_cap():
    from memory.ops import gap_scheduler
    import memory.ops.dispatcher as dispatcher_module

    # Same "keep questions unrelated" note as the test above -- these must
    # stay three distinct rows, not collapse into one recurring gap.
    ledger = _fresh_ledger()
    questions = [
        "Which database connection pooling library does this service use?",
        "Where are feature flags configured for the checkout flow?",
        "What retry policy does the payment webhook handler use?",
    ]
    gaps = [ledger.log_gap(q, priority="high") for q in questions]

    dispatched = []

    def fake_dispatch(gap_id):
        dispatched.append(gap_id)
        return {"ok": True, "gap_id": gap_id}

    async def _run():
        with _patched(
            gap_scheduler,
            get_gap_ledger=lambda: ledger,
            gap_auto_sweep_enabled=lambda: True,
            gap_sweep_batch_size=lambda: 5,   # batch would allow all 3...
            gap_sweep_max_concurrent=lambda: 1,  # ...but concurrency caps it to 1
        ), _patched(dispatcher_module, dispatch_gap_investigation=fake_dispatch):
            summary = await gap_scheduler.sweep_once()
            await _drain_pending_tasks()
        return summary

    summary = asyncio.run(_run())
    assert summary["eligible"] == 3
    assert summary["dispatched"] == 1
    assert summary["skipped"] == 2
    assert len(dispatched) == 1 and dispatched[0] in {g["gap_id"] for g in gaps}
    assert gap_scheduler._running_gap_ids == set()


def test_sweep_once_is_a_noop_when_auto_sweep_disabled():
    from memory.ops import gap_scheduler
    import memory.ops.dispatcher as dispatcher_module

    ledger = _fresh_ledger()
    ledger.log_gap("A high-priority gap that must NOT be touched", priority="high")

    dispatched = []

    async def _run():
        with _patched(gap_scheduler, get_gap_ledger=lambda: ledger, gap_auto_sweep_enabled=lambda: False), \
             _patched(dispatcher_module, dispatch_gap_investigation=lambda gap_id: dispatched.append(gap_id)):
            summary = await gap_scheduler.sweep_once()
            await _drain_pending_tasks()
        return summary

    summary = asyncio.run(_run())
    assert summary == {"eligible": 0, "dispatched": 0, "skipped": 0}
    assert dispatched == []


def test_sweep_once_never_raises_even_if_ledger_lookup_fails():
    """A failed sweep tick must never take down the long-running scheduler
    loop -- see run_periodic_gap_sweep's try/except around each tick."""
    from memory.ops import gap_scheduler

    def _boom():
        raise RuntimeError("ledger unavailable")

    async def _run():
        with _patched(gap_scheduler, get_gap_ledger=_boom, gap_auto_sweep_enabled=lambda: True):
            return await gap_scheduler.sweep_once()

    summary = asyncio.run(_run())
    assert summary == {"eligible": 0, "dispatched": 0, "skipped": 0}


def test_run_periodic_gap_sweep_recovers_once_then_sweeps_on_the_configured_interval():
    from memory.ops import gap_scheduler

    class _StopLoop(Exception):
        pass

    calls = []

    def fake_recover():
        calls.append(("recover",))
        return 0

    async def fake_sweep_once():
        calls.append(("sweep",))
        return {"eligible": 0, "dispatched": 0, "skipped": 0}

    async def fake_sleep(seconds):
        calls.append(("sleep", seconds))
        raise _StopLoop()

    async def _run():
        with _patched(
            gap_scheduler,
            recover_stale_investigating_gaps=fake_recover,
            sweep_once=fake_sweep_once,
            heavy_ops_enabled=lambda: True,
            gap_sweep_interval_hours=lambda: 6,
        ), _patched(asyncio, sleep=fake_sleep):
            try:
                await gap_scheduler.run_periodic_gap_sweep()
            except _StopLoop:
                pass

    asyncio.run(_run())
    assert calls == [("recover",), ("sweep",), ("sleep", 6 * 3600)]


def test_run_periodic_gap_sweep_skips_sweep_tick_when_heavy_ops_disabled():
    """The loop itself always runs (started unconditionally at gateway
    startup); it's each TICK that no-ops when heavy ops is off for this
    install -- confirms sweep_once is never even called in that case,
    not just that it would no-op internally."""
    from memory.ops import gap_scheduler

    class _StopLoop(Exception):
        pass

    calls = []

    async def fake_sweep_once():
        calls.append("sweep")
        return {"eligible": 0, "dispatched": 0, "skipped": 0}

    async def fake_sleep(seconds):
        calls.append(("sleep", seconds))
        raise _StopLoop()

    async def _run():
        with _patched(
            gap_scheduler,
            recover_stale_investigating_gaps=lambda: 0,
            sweep_once=fake_sweep_once,
            heavy_ops_enabled=lambda: False,
            gap_sweep_interval_hours=lambda: 24,
        ), _patched(asyncio, sleep=fake_sleep):
            try:
                await gap_scheduler.run_periodic_gap_sweep()
            except _StopLoop:
                pass

    asyncio.run(_run())
    assert "sweep" not in calls
    assert ("sleep", 24 * 3600) in calls


def _run_all():
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"OK  {t.__name__}")
    print(f"\nALL {len(tests)} LAYER 2B/GAP-LEDGER CHECKS PASSED")


if __name__ == "__main__":
    _run_all()
