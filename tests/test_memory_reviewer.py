"""
Sanity checks for the `remember` review gate (gateway/memory/ops/reviewer.py)
-- the synchronous, fail-closed LLM check that runs before any in-session
`remember` call is persisted (design doc SS11 "Active (in-turn, high
precision)").

Run with (host, conda env with gateway deps):
    python tests/test_memory_reviewer.py
"""
import os
import sys
import json
import types
import pathlib
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AURORACODER_DATA_DIR", tempfile.mkdtemp())
os.environ.setdefault("AURORACODER_DOCKER", "0")

from gateway.memory.schema import MemoryItem
from gateway.memory.store import MemoryRepository
from gateway.memory.ops import reviewer


class _FakeClient:
    def __init__(self, payload):
        choice = types.SimpleNamespace(message=types.SimpleNamespace(content=payload))
        response = types.SimpleNamespace(choices=[choice])

        class _Completions:
            def create(self_inner, **kwargs):
                return response

        self.chat = types.SimpleNamespace(completions=_Completions())


def _patch_reviewer(payload):
    reviewer.get_memory_extraction_config = lambda: {
        "provider_id": "fake", "base_url": "http://fake", "api_key": "fake-key", "model": "fake-model",
    }
    reviewer.OpenAI = lambda base_url, api_key: _FakeClient(payload)


def test_review_disabled_auto_approves_without_calling_model():
    calls = []
    reviewer.get_other_settings = lambda: {"memory": {"remember_review_enabled": False}}
    reviewer.OpenAI = lambda base_url, api_key: calls.append(1)
    decision = reviewer.review_candidate({"content": "x", "description": "y", "plane": "world"}, [])
    assert decision["decision"] == "approve"
    assert not calls, "must not call the model when review is disabled"
    reviewer.get_other_settings = lambda: {}  # restore default (enabled)


def test_review_approves_good_candidate():
    payload = json.dumps({"decision": "approve", "reason": "durable, non-derivable preference",
                           "duplicate_of": None, "adjusted_plane": None, "adjusted_confidence": None})
    _patch_reviewer(payload)
    decision = reviewer.review_candidate(
        {"content": "Run ruff before finishing.", "description": "User preference", "plane": "stance"}, [],
    )
    assert decision["decision"] == "approve"


def test_review_rejects_low_value_candidate():
    payload = json.dumps({"decision": "reject", "reason": "derivable from AGENTS.md",
                           "duplicate_of": None, "adjusted_plane": None, "adjusted_confidence": None})
    _patch_reviewer(payload)
    decision = reviewer.review_candidate(
        {"content": "The project uses Python.", "description": "Language used", "plane": "world"}, [],
    )
    assert decision["decision"] == "reject"
    assert "AGENTS.md" in decision["reason"]


def test_review_can_demote_stance_to_world():
    payload = json.dumps({"decision": "approve", "reason": "not durable enough for stance",
                           "duplicate_of": None, "adjusted_plane": "world", "adjusted_confidence": "medium"})
    _patch_reviewer(payload)
    decision = reviewer.review_candidate(
        {"content": "Maybe prefers concise answers?", "description": "Possible communication preference", "plane": "stance"}, [],
    )
    assert decision["decision"] == "approve"
    assert decision["adjusted_plane"] == "world"
    assert decision["adjusted_confidence"] == "medium"


def test_review_can_flag_duplicate():
    payload = json.dumps({"decision": "approve", "reason": "refines existing memory",
                           "duplicate_of": "mem_existing123", "adjusted_plane": None, "adjusted_confidence": None})
    _patch_reviewer(payload)
    decision = reviewer.review_candidate(
        {"content": "Pipeline bugs tracked in Linear INGEST, escalate P0s to #incidents.", "description": "Pipeline bug tracking", "plane": "world"},
        [{"id": "mem_existing123", "description": "Pipeline bugs tracked in Linear INGEST"}],
    )
    assert decision["decision"] == "approve"
    assert decision["duplicate_of"] == "mem_existing123"


def test_review_fails_closed_on_unparsable_output():
    _patch_reviewer("this is not json at all")
    decision = reviewer.review_candidate({"content": "x", "description": "y", "plane": "world"}, [])
    assert decision["decision"] == "reject"


def test_review_fails_closed_on_missing_provider():
    reviewer.get_memory_extraction_config = lambda: {"provider_id": "", "base_url": "", "api_key": "", "model": ""}
    decision = reviewer.review_candidate({"content": "x", "description": "y", "plane": "world"}, [])
    assert decision["decision"] == "reject"
    assert "provider" in decision["reason"]


def test_review_fails_closed_on_exception():
    def _boom():
        raise RuntimeError("network down")
    reviewer.get_memory_extraction_config = _boom
    decision = reviewer.review_candidate({"content": "x", "description": "y", "plane": "world"}, [])
    assert decision["decision"] == "reject"


def test_find_similar_existing_matches_overlapping_descriptions():
    repo = MemoryRepository(storage_dir=pathlib.Path(tempfile.mkdtemp()))
    a = MemoryItem(content="A", description="Pipeline bugs tracked in Linear INGEST project",
                    plane="world", type="reference", scope="project")
    b = MemoryItem(content="B", description="Completely unrelated deployment fact",
                    plane="world", type="reference", scope="project")
    repo.upsert(a)
    repo.upsert(b)

    similar = reviewer.find_similar_existing(repo, plane="world", scope="project",
                                              description="pipeline bugs tracked in linear ingest")
    ids = [s["id"] for s in similar]
    assert a.id in ids
    assert b.id not in ids


def test_route_rejects_write_when_reviewer_rejects():
    from fastapi.testclient import TestClient
    from gateway.api import app
    import gateway.routes as routes

    payload = json.dumps({"decision": "reject", "reason": "not durable enough",
                           "duplicate_of": None, "adjusted_plane": None, "adjusted_confidence": None})
    _patch_reviewer(payload)
    # routes.py did `from ...reviewer import review_candidate`, so it holds the
    # same function object patched above -- no separate patch needed here.
    assert routes.review_candidate is reviewer.review_candidate

    client = TestClient(app)
    r = client.post("/api/memory/remember", json={
        "content": "The current bug is in file X.", "description": "ephemeral task state",
        "plane": "world", "type": "reference", "scope": "project",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "rejected by review" in body["reason"]

    r = client.get("/api/memory")
    assert all("ephemeral task state" not in m.get("description", "") for m in r.json()["memories"])


def _run_all():
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"OK  {t.__name__}")
    print(f"\nALL {len(tests)} REMEMBER-REVIEW-GATE CHECKS PASSED")


if __name__ == "__main__":
    _run_all()
