"""
Sanity checks for Memory Layer 2b scaffolding: the Gap Ledger (light,
always on) and the memory-worker dispatcher (heavy, disabled by default).

The dispatcher tests never invoke real docker or touch the filesystem
outside an isolated temp dir -- subprocess/shutil/workspace access are
all monkeypatched. Safe to run alongside a live AuroraCoder container.

Run with (host, conda env with gateway deps):
    python tests/test_memory_layer3.py
"""
import os
import sys
import json
import pathlib
import tempfile
from contextlib import contextmanager

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AURORACODER_DATA_DIR", tempfile.mkdtemp())
os.environ.setdefault("AURORACODER_DOCKER", "0")

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
    (see get_filtered_tools in src/web_api/app.py) -- assert that mode
    resolves to exactly the minimal read-oriented set plus report_findings,
    never remember/recall/forget/log_gap/subagent/write_file/edit_file."""
    from src.web_api.app import get_filtered_tools
    from src.tool_definitions import GAP_INVESTIGATION_TOOLS

    names = {td["function"]["name"] for td in get_filtered_tools("gap_investigation")}
    assert names == GAP_INVESTIGATION_TOOLS
    assert "report_findings" in names
    assert not names & {"remember", "recall", "log_gap", "forget", "subagent", "write_file", "edit_file", "delete_file"}


def _run_all():
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"OK  {t.__name__}")
    print(f"\nALL {len(tests)} LAYER 2B/GAP-LEDGER CHECKS PASSED")


if __name__ == "__main__":
    _run_all()
