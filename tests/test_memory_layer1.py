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
os.environ.setdefault("AURORACODER_DOCKER", "0")

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


def _run_all():
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"OK  {t.__name__}")
    print(f"\nALL {len(tests)} LAYER 1 CHECKS PASSED")


if __name__ == "__main__":
    _run_all()
