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
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AURORACODER_DATA_DIR", tempfile.mkdtemp())
os.environ.setdefault("AURORACODER_DOCKER", "0")

from gateway.memory.schema import MemoryItem
from gateway.memory.store import MemoryRepository


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
    ids = [m["id"] for m in r.json()["memories"]]
    assert mem_id in ids
    r = client.delete(f"/api/memory/{mem_id}")
    assert r.status_code == 200
    r = client.delete(f"/api/memory/{mem_id}")
    assert r.status_code == 404


def _run_all():
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"OK  {t.__name__}")
    print(f"\nALL {len(tests)} LAYER 1 CHECKS PASSED")


if __name__ == "__main__":
    _run_all()
