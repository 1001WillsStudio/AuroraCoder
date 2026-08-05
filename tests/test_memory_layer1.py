"""
Sanity checks for Memory Layer 1 (light runtime): schema/store round-trip
plus the gateway /api/memory/* routes, exercised in-process via
fastapi.testclient (no real port bound — safe to run alongside a live
AuroraCoder container without touching it).

Uses an isolated temp AURORACODER_DATA_DIR so it never reads/writes a
real local or container data directory.

Run with (host, conda env with gateway deps — see gateway/requirements.txt):
    python tests/test_memory_layer1.py
"""
import os
import sys
import json
import pathlib
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AURORACODER_DATA_DIR", tempfile.mkdtemp())
# FORCE (not setdefault) — inside a container AURORACODER_DOCKER is already
# exported as "1", so setdefault would no-op and gateway.settings_store would
# bind DATA_DIR to /app/data, ignoring this suite's isolated AURORACODER_DATA_DIR
# and reading the real /app/data/settings.json instead of the temp one below.
os.environ["AURORACODER_DOCKER"] = "0"

# Memory is opt-in (settings.other.memory.enabled defaults to False — see
# memory/settings.py). This suite specifically exercises the enabled path,
# so seed settings.json before any gateway module is imported. The
# disabled/no-op path is covered separately in test_memory_toggle.py.
pathlib.Path(os.environ["AURORACODER_DATA_DIR"]).mkdir(parents=True, exist_ok=True)
pathlib.Path(os.environ["AURORACODER_DATA_DIR"], "settings.json").write_text(
    json.dumps({"other": {"memory": {"enabled": True}}}), encoding="utf-8"
)

from memory.schema import MemoryItem
from memory.store import MemoryRepository


def test_schema_markdown_roundtrip():
    item = MemoryItem(
        content="Always run ruff before committing.",
        description="User preference: run ruff before commits",
        plane="stance",
        type="preference",
        scope="user",
        confidence="high",
        provenance="user stated",
    )
    md = item.to_markdown()
    restored = MemoryItem.from_markdown(md)
    assert restored.content == item.content
    assert restored.plane == item.plane
    assert restored.type == item.type
    assert restored.confidence == item.confidence


def test_store_crud():
    import pathlib
    repo = MemoryRepository(storage_dir=pathlib.Path(tempfile.mkdtemp()))
    item = MemoryItem(content="X", description="Y", plane="world", type="reference", scope="project")
    repo.upsert(item)
    assert repo.get(item.id) is not None
    ids = [m["id"] for m in repo.list(plane="world")]
    assert item.id in ids
    repo.delete(item.id)
    assert repo.get(item.id) is None


def test_gateway_routes():
    from fastapi.testclient import TestClient
    from gateway.api import app

    # /api/memory/remember is a plain, unreviewed direct write (the agent's
    # `remember` tool no longer calls it at runtime — see
    # test_memory_layer2.py for the unified end-of-session judgment pass
    # that now handles both agent-nominated and discovered candidates).
    client = TestClient(app)

    r = client.get("/api/memory/stance")
    assert r.status_code == 200, r.text
    assert r.json()["stance"] == ""

    r = client.post("/api/memory/remember", json={
        "content": "Always run ruff before committing.",
        "description": "User preference: run ruff before commits",
        "plane": "stance", "type": "preference", "scope": "user", "confidence": "high",
    })
    assert r.status_code == 200, r.text
    mem_id = r.json()["id"]

    r = client.get("/api/memory/stance")
    assert "ruff" in r.json()["stance"]

    r = client.post("/api/memory/remember", json={
        "content": "Pipeline bugs are tracked in Linear project INGEST.",
        "description": "Where pipeline bugs are tracked",
        "plane": "world", "type": "reference", "scope": "project", "confidence": "high",
    })
    assert r.status_code == 200, r.text

    r = client.get("/api/memory/recall", params={"query": "pipeline bug tracker", "plane": "world"})
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert len(results) == 1 and "INGEST" in results[0]["content"], results

    r = client.post("/api/memory/remember", json={"content": "x", "description": "y", "plane": "bogus"})
    assert r.status_code == 400

    r = client.get("/api/memory")
    by_id = {m["id"]: m for m in r.json()["memories"]}
    assert mem_id in by_id
    assert by_id[mem_id]["content"] == "Always run ruff before committing."  # full content, not just metadata

    r = client.delete(f"/api/memory/{mem_id}")
    assert r.status_code == 200 and r.json()["ok"] is True
    r = client.delete(f"/api/memory/{mem_id}")
    assert r.status_code == 404


def test_forget_tool_success_and_failure_messages():
    """Unit-level: forget_tool formats memory_client.forget()'s result
    correctly, for both the happy path and a not-found id."""
    from src.core_tools import memory_client
    from src.core_tools.memory_tools import forget_tool

    orig_forget = memory_client.forget
    try:
        memory_client.forget = lambda memory_id: {"ok": True, "deleted": memory_id}
        text, _ = forget_tool({"memory_id": "mem_abc123"})
        assert "Forgot memory mem_abc123" in text

        memory_client.forget = lambda memory_id: {"ok": False, "error": "no memory with that id"}
        text, _ = forget_tool({"memory_id": "mem_nope"})
        assert "Could not forget" in text and "no memory with that id" in text
    finally:
        memory_client.forget = orig_forget


def test_recall_tool_output_includes_memory_id():
    """recall's formatted text must expose ids so a follow-up `forget`
    call can target the exact memory instead of guessing."""
    from src.core_tools import memory_client
    from src.core_tools.memory_tools import recall_tool

    orig_recall = memory_client.recall
    try:
        memory_client.recall = lambda **kw: [
            {"id": "mem_xyz789", "type": "preference", "description": "desc", "confidence": "high", "content": "content"}
        ]
        text, _ = recall_tool({"query": "anything"})
        assert "id=mem_xyz789" in text
    finally:
        memory_client.recall = orig_recall


def test_forget_route_disabled_when_memory_disabled():
    from fastapi.testclient import TestClient
    from gateway.api import app

    client = TestClient(app)
    r = client.post("/api/memory/remember", json={
        "content": "x", "description": "y", "plane": "world", "type": "reference", "scope": "project",
    })
    memory_id = r.json()["id"]

    settings_path = pathlib.Path(os.environ["AURORACODER_DATA_DIR"]) / "settings.json"
    orig_settings = settings_path.read_text(encoding="utf-8")
    try:
        settings_path.write_text(json.dumps({"other": {"memory": {"enabled": False}}}), encoding="utf-8")
        r = client.delete(f"/api/memory/{memory_id}")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False and "disabled" in body["reason"]
    finally:
        settings_path.write_text(orig_settings, encoding="utf-8")

    # Re-enabled: deletion works again, and the memory from before is still there.
    r = client.delete(f"/api/memory/{memory_id}")
    assert r.status_code == 200 and r.json()["ok"] is True


# ---------------------------------------------------------------------------
# Regression tests for the usage/recency source-of-truth fix:
# bump_usage() only updates the SQLite index, while ranking (retrieval.py),
# eviction (enforce_capacity) and the consolidation/extraction corpus read
# MemoryItem objects hydrated from the markdown files. All read paths must
# therefore hydrate usage_count/last_used from the index (store.get /
# all_items), and upsert-on-conflict must never roll the index values back.
# ---------------------------------------------------------------------------

def test_bump_usage_is_visible_to_get_and_all_items():
    """A retrieval hit recorded by bump_usage() must be visible on every
    read path — otherwise ranking, eviction and decay judge by a counter
    that froze at the last upsert."""
    repo = MemoryRepository(storage_dir=pathlib.Path(tempfile.mkdtemp()))
    item = MemoryItem(content="X", description="deploy checklist lives in Notion",
                      plane="world", type="reference", scope="project")
    repo.upsert(item)
    assert repo.get(item.id).usage_count == 0

    repo.bump_usage([item.id])
    repo.bump_usage([item.id])

    fetched = repo.get(item.id)
    assert fetched.usage_count == 2, fetched.usage_count
    assert fetched.last_used is not None

    items = repo.all_items(plane="world")
    assert len(items) == 1
    assert items[0].usage_count == 2, items[0].usage_count
    assert items[0].last_used is not None


def test_upsert_update_never_resets_usage_history():
    """Re-upserting an existing id (the dedup / in-place-update path) must
    not roll usage_count/last_used back to whatever the item object carries
    — the index row always wins."""
    repo = MemoryRepository(storage_dir=pathlib.Path(tempfile.mkdtemp()))
    item = MemoryItem(content="Old wording", description="convention: ruff before commit",
                      plane="world", type="convention", scope="project")
    repo.upsert(item)
    repo.bump_usage([item.id])
    repo.bump_usage([item.id])

    # An update arriving with a stale/zero usage counter (e.g. a direct
    # write, or any write pass that didn't preserve history).
    update = MemoryItem(content="New wording", description="convention: ruff before commit",
                        plane="world", type="convention", scope="project",
                        id=item.id, usage_count=0, last_used=None)
    repo.upsert(update)

    fetched = repo.get(item.id)
    assert fetched.content == "New wording"
    assert fetched.usage_count == 2, fetched.usage_count
    assert fetched.last_used is not None


def test_recall_bumps_make_ranking_and_eviction_usage_aware():
    """End-to-end consequence of the hydration fix: a memory that was
    recalled many times must outrank and outlive a newer never-used one.
    Before the fix, bumps only touched the SQLite index while ranking and
    eviction read stale file values — so usage had zero effect and
    eviction was effectively oldest-created-first."""
    from datetime import datetime, timedelta, timezone
    from memory.retrieval import rank_candidates

    repo = MemoryRepository(storage_dir=pathlib.Path(tempfile.mkdtemp()))
    now = datetime.now(timezone.utc)
    old = MemoryItem(content="legacy runbook", description="how to restart the ingest pipeline",
                     plane="world", type="reference", scope="project",
                     created=(now - timedelta(days=200)).isoformat())
    new = MemoryItem(content="recent note about the ingest pipeline", description="ingest pipeline trivia",
                     plane="world", type="project", scope="project",
                     created=(now - timedelta(days=20)).isoformat())
    repo.upsert(old)
    repo.upsert(new)
    for _ in range(3):
        repo.bump_usage([old.id])

    # Ranking: with the 3 bumps old must beat the newer, equally-relevant
    # note. Without hydration, old scores 0.30 (keyword only) vs new's
    # ~0.43 (keyword + recency) and loses.
    ranked = rank_candidates(repo, query="restart the ingest pipeline", plane="world")
    assert ranked and ranked[0]["id"] == old.id, [(r["id"], r["score"]) for r in ranked]

    # Eviction: cap=1 forces exactly one eviction. The never-used newer
    # memory must go — before the fix both files showed usage 0 and the
    # older (but actively-used) memory was evicted instead.
    evicted = repo.enforce_capacity(cap=1)
    assert evicted == 1, evicted
    remaining = [it.id for it in repo.all_items(plane="world")]
    assert remaining == [old.id], remaining


def _run_all():
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"OK  {t.__name__}")
    print(f"\nALL {len(tests)} LAYER 1 CHECKS PASSED")


if __name__ == "__main__":
    _run_all()
