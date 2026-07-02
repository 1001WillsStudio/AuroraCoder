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
import pathlib
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AURORACODER_DATA_DIR", tempfile.mkdtemp())
os.environ.setdefault("AURORACODER_DOCKER", "0")

from gateway.memory.gap_store import GapLedger
from gateway.memory.ops import dispatcher


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
        from gateway.memory.gap_store import get_gap_ledger
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


def _run_all():
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"OK  {t.__name__}")
    print(f"\nALL {len(tests)} LAYER 2B/GAP-LEDGER CHECKS PASSED")


if __name__ == "__main__":
    _run_all()
