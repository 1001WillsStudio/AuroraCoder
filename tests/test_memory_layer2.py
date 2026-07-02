"""
Sanity checks for Memory Layer 2a (passive pipeline): extraction prompt
plumbing (with a fake OpenAI client, no network) and the consolidator's
dedupe/decay heuristics.

Run with (host, conda env with gateway deps):
    python tests/test_memory_layer2.py
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
from gateway.memory.ops import extractor, consolidator as C

_SAMPLE_MSGS = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "Always run ruff before you say you're done."},
    {"role": "assistant", "content": "Got it, I'll run ruff going forward.", "tool_calls": []},
]


class _FakeClient:
    """Mimics openai.OpenAI's chat.completions.create() surface with a canned payload."""

    def __init__(self, payload):
        choice = types.SimpleNamespace(message=types.SimpleNamespace(content=payload))
        response = types.SimpleNamespace(choices=[choice])

        class _Completions:
            def create(self_inner, **kwargs):
                return response

        self.chat = types.SimpleNamespace(completions=_Completions())


def _patch_extractor(payload):
    extractor.get_memory_extraction_config = lambda: {
        "provider_id": "fake", "base_url": "http://fake", "api_key": "fake-key", "model": "fake-model",
    }
    extractor.OpenAI = lambda base_url, api_key: _FakeClient(payload)


def test_transcript_rendering():
    text = extractor._transcript_to_text(_SAMPLE_MSGS)
    assert "USER: Always run ruff" in text


def test_json_extraction_helper():
    assert extractor._extract_json('{"memories": []}') == {"memories": []}
    assert extractor._extract_json('noise {"memories": [{"a": 1}]} noise') == {"memories": [{"a": 1}]}
    assert extractor._extract_json("not json") is None


def test_extraction_no_op_by_default():
    _patch_extractor('{"memories": []}')
    result = extractor.run_extraction("conv-1234", _SAMPLE_MSGS * 2)
    assert result == [], result


def test_extraction_writes_valid_candidate():
    payload = json.dumps({"memories": [{
        "plane": "stance", "type": "preference", "scope": "user",
        "content": "Run ruff before declaring a task done.",
        "description": "User preference: run ruff before finishing",
        "confidence": "high",
    }]})
    _patch_extractor(payload)
    result = extractor.run_extraction("conv-5678", _SAMPLE_MSGS * 2)
    assert len(result) == 1, result
    saved = extractor.get_repository().get(result[0])
    assert saved.content.startswith("Run ruff")


def test_extraction_skips_malformed_candidate():
    payload = json.dumps({"memories": [{"plane": "bogus", "type": "preference"}]})
    _patch_extractor(payload)
    result = extractor.run_extraction("conv-9999", _SAMPLE_MSGS * 2)
    assert result == []


def test_extraction_skips_short_transcripts_without_calling_model():
    calls = []
    extractor.get_memory_extraction_config = lambda: {
        "provider_id": "fake", "base_url": "http://fake", "api_key": "fake-key", "model": "fake-model",
    }
    extractor.OpenAI = lambda base_url, api_key: calls.append(1) or _FakeClient('{"memories":[]}')
    result = extractor.run_extraction("conv-short", _SAMPLE_MSGS[:2])
    assert result == [] and not calls, "must not call the model for trivially short transcripts"


def _fresh_repo() -> MemoryRepository:
    return MemoryRepository(storage_dir=pathlib.Path(tempfile.mkdtemp()))


def test_dedupe_keeps_higher_usage_duplicate():
    repo = _fresh_repo()
    a = MemoryItem(content="A", description="Pipeline bugs tracked in Linear INGEST project",
                    plane="world", type="reference", scope="project", confidence="low")
    b = MemoryItem(content="B", description="pipeline bugs tracked in linear ingest project.",
                    plane="world", type="reference", scope="project", confidence="low", usage_count=3)
    c = MemoryItem(content="C", description="Completely unrelated fact about deployment",
                    plane="world", type="reference", scope="project", confidence="low")
    repo.upsert(a)
    repo.upsert(b)
    repo.upsert(c)

    merged = C.dedupe_world_memories(repo)
    assert merged == 1, merged
    remaining = {m["id"] for m in repo.list(plane="world")}
    assert b.id in remaining and a.id not in remaining and c.id in remaining


def test_decay_drops_stale_low_confidence_only():
    repo = _fresh_repo()
    stale = MemoryItem(content="stale", description="stale fact nobody used",
                        plane="world", type="reference", scope="project", confidence="low")
    stale.created = "2020-01-01T00:00:00+00:00"
    repo.upsert(stale)

    important = MemoryItem(content="important", description="important old fact",
                            plane="world", type="reference", scope="project", confidence="high")
    important.created = "2020-01-01T00:00:00+00:00"
    repo.upsert(important)

    decayed = C.decay_unused_world_memories(repo, max_unused_days=90)
    assert decayed == 1, decayed
    assert repo.get(stale.id) is None
    assert repo.get(important.id) is not None


def test_decay_never_touches_stance_plane():
    repo = _fresh_repo()
    stance_item = MemoryItem(content="pref", description="a stance pref", plane="stance",
                              type="preference", scope="user", confidence="low")
    stance_item.created = "2020-01-01T00:00:00+00:00"
    repo.upsert(stance_item)
    C.decay_unused_world_memories(repo, max_unused_days=1)
    assert repo.get(stance_item.id) is not None


def _run_all():
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"OK  {t.__name__}")
    print(f"\nALL {len(tests)} LAYER 2 CHECKS PASSED")


if __name__ == "__main__":
    _run_all()
