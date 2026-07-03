"""
Sanity checks for Memory Layer 2a — the unified end-of-session pass that
now handles BOTH agent-nominated candidates (from mid-session `remember`
calls, which do no I/O at call time — see src/core_tools/memory_tools.py)
and discovered candidates, in one pipeline (with a fake OpenAI client, no
network), plus the consolidator's dedupe/decay heuristics.

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
from memory.ops import extractor, consolidator as C
from memory.ops import conversation_search
from gateway.conversation_store import store as conv_store

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


def test_prompt_renders_other_conversation_snippets():
    from memory.ops.prompts import build_extraction_user_prompt

    nominated = [{"description": "desc", "content": "c", "plane": "world", "type": "project", "scope": "project"}]
    other = [[{"conversation_id": "conv-old", "title": "Old chat", "snippet": "ruff stuff", "score": 0.4}]]
    prompt = build_extraction_user_prompt("transcript", nominated, [[]], other)
    assert "conv-old" in prompt and "ruff stuff" in prompt


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


# ---------------------------------------------------------------------------
# Agent-nominated candidates (from mid-session `remember` calls, which do no
# I/O — this pass is the only place they actually get judged and written).
# ---------------------------------------------------------------------------

_REMEMBER_ARGS = {
    "content": "User wants ruff run before declaring any task done.",
    "description": "User preference: run ruff before finishing",
    "plane": "stance", "type": "preference", "scope": "user", "confidence": "high",
}

_MSGS_WITH_REMEMBER_CALL = [
    {"role": "user", "content": "Always run ruff before you say you're done."},
    {
        "role": "assistant", "content": "Got it.",
        "tool_calls": [{
            "id": "call_1",
            "function": {"name": "remember", "arguments": json.dumps(_REMEMBER_ARGS)},
        }],
    },
    {"role": "tool", "content": "Noted", "tool_call_id": "call_1"},
]


def test_nominated_candidates_parsed_from_transcript():
    nominated = extractor._extract_nominated_candidates(_MSGS_WITH_REMEMBER_CALL)
    assert len(nominated) == 1
    assert nominated[0]["description"] == _REMEMBER_ARGS["description"]
    assert nominated[0]["plane"] == "stance"


def test_nominated_candidates_skip_malformed_calls():
    bad_msgs = [{
        "role": "assistant", "content": "",
        "tool_calls": [{"id": "c1", "function": {"name": "remember", "arguments": "not json"}}],
    }]
    assert extractor._extract_nominated_candidates(bad_msgs) == []

    missing_fields_msgs = [{
        "role": "assistant", "content": "",
        "tool_calls": [{"id": "c1", "function": {"name": "remember", "arguments": json.dumps({"content": "x"})}}],
    }]
    assert extractor._extract_nominated_candidates(missing_fields_msgs) == [], \
        "a candidate missing 'description' must be dropped, not passed through with a placeholder"


def test_nomination_bypasses_min_messages_gate():
    """A single short exchange that ends in a `remember` call must still be
    sent to the model — the min-messages gate exists to skip trivial
    transcripts with nothing to mine, not to drop an explicit nomination."""
    payload = json.dumps({"memories": [{
        "plane": "stance", "type": "preference", "scope": "user", "source": "nominated",
        "content": _REMEMBER_ARGS["content"], "description": _REMEMBER_ARGS["description"], "confidence": "high",
    }]})
    _patch_extractor(payload)
    result = extractor.run_extraction("conv-nom", _MSGS_WITH_REMEMBER_CALL)
    assert len(result) == 1, result


def test_nominated_candidate_can_be_rejected_by_the_model():
    """Nomination is a request to consider, not an instruction to save —
    the no-op default still applies to nominated candidates."""
    _patch_extractor('{"memories": []}')
    result = extractor.run_extraction("conv-nom-reject", _MSGS_WITH_REMEMBER_CALL)
    assert result == [], result


def test_nominated_duplicate_of_updates_existing_memory_in_place():
    repo = extractor.get_repository()
    existing = MemoryItem(content="Old wording", description="User preference: run ruff before finishing",
                           plane="stance", type="preference", scope="user", confidence="medium")
    existing.usage_count = 4
    existing.last_used = "2024-01-01T00:00:00+00:00"
    repo.upsert(existing)

    payload = json.dumps({"memories": [{
        "plane": "stance", "type": "preference", "scope": "user", "source": "nominated",
        "content": "Updated wording — run ruff before finishing, always.",
        "description": "User preference: run ruff before finishing",
        "confidence": "high", "duplicate_of": existing.id,
    }]})
    _patch_extractor(payload)
    result = extractor.run_extraction("conv-nom-dup", _MSGS_WITH_REMEMBER_CALL)
    assert result == [existing.id], result
    updated = repo.get(existing.id)
    assert updated.content.startswith("Updated wording")
    # Reinforcing an existing memory must not erase the usage history that
    # decay/retention judges it by, and must bump corroboration_count — a
    # deterministic, code-computed signal (see ops/consolidator.py) that a
    # self-reported confidence field can't fake.
    assert updated.usage_count == 4, updated.usage_count
    assert updated.last_used == "2024-01-01T00:00:00+00:00"
    assert updated.corroboration_count == 1, updated.corroboration_count


# ---------------------------------------------------------------------------
# Cross-conversation search (ops/conversation_search.py) — the shared,
# deterministic utility both the write-pass (in-process) and, eventually,
# Layer 2b's gap-investigation worker (over HTTP) are meant to use, rather
# than each growing its own reach into all stored conversation history.
# ---------------------------------------------------------------------------

def _seed_conversation(cid: str, title_msg: str, other_msgs=None):
    conv_store.create_conversation(conversation_id=cid, conv_type="user_chat")
    msgs = [{"role": "user", "content": title_msg}] + (other_msgs or [])
    conv_store.save_messages(cid, msgs)


def test_conversation_search_finds_relevant_past_conversation():
    _seed_conversation("conv-a", "I always want ruff run before you finish any task.")
    _seed_conversation("conv-b", "Completely unrelated chat about deployment scripts.")

    results = conversation_search.search_conversations("run ruff before finishing", limit=5)
    ids = [r["conversation_id"] for r in results]
    assert "conv-a" in ids
    assert "conv-b" not in ids


def test_conversation_search_excludes_specified_conversation():
    _seed_conversation("conv-c", "Run ruff before declaring anything done.")
    results = conversation_search.search_conversations("run ruff before finishing", exclude_conversation_id="conv-c")
    assert all(r["conversation_id"] != "conv-c" for r in results)


def test_conversation_search_empty_query_returns_empty():
    assert conversation_search.search_conversations("") == []


def test_extraction_calls_conversation_search_per_nomination():
    calls = []

    def _fake_search(query, exclude_conversation_id=None, limit=3):
        calls.append((query, exclude_conversation_id))
        return [{"conversation_id": "conv-old", "title": "old chat", "snippet": "…", "score": 0.5}]

    orig = extractor.search_conversations
    extractor.search_conversations = _fake_search
    try:
        _patch_extractor('{"memories": []}')
        extractor.run_extraction("conv-current", _MSGS_WITH_REMEMBER_CALL)
    finally:
        extractor.search_conversations = orig

    assert len(calls) == 1
    query, excluded = calls[0]
    assert query == _REMEMBER_ARGS["description"]
    assert excluded == "conv-current"


def test_remember_tool_is_a_pure_local_noop():
    """The agent-facing `remember` tool must never touch the network — it
    only leaves a marker in the transcript for the pass above to parse."""
    import requests
    from src.core_tools import memory_tools

    def _fail_if_called(*a, **kw):
        raise AssertionError("remember_tool must not perform any HTTP call")

    orig_get, orig_post = requests.get, requests.post
    requests.get, requests.post = _fail_if_called, _fail_if_called
    try:
        msg, echoed = memory_tools.remember_tool(_REMEMBER_ARGS)
    finally:
        requests.get, requests.post = orig_get, orig_post

    assert "end of this session" in msg
    assert echoed is _REMEMBER_ARGS


def _fresh_repo() -> MemoryRepository:
    return MemoryRepository(storage_dir=pathlib.Path(tempfile.mkdtemp()))


def _iso_days_ago(days: int) -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


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


def test_decay_low_confidence_drops_faster_than_medium():
    """Self-reported confidence is only trusted to shorten life (low ->
    0.5x grace), never to lengthen it — see consolidator module docstring.
    At age=60d with a 90d base grace: low's effective grace is 45d (decays),
    medium's stays 90d (survives)."""
    repo = _fresh_repo()
    stale = MemoryItem(content="stale", description="a shaky fact nobody used",
                        plane="world", type="reference", scope="project", confidence="low")
    stale.created = _iso_days_ago(60)
    repo.upsert(stale)

    ok = MemoryItem(content="ok", description="an ordinary fact nobody used",
                     plane="world", type="reference", scope="project", confidence="medium")
    ok.created = _iso_days_ago(60)
    repo.upsert(ok)

    decayed = C.decay_unused_world_memories(repo, max_unused_days=90)
    assert decayed == 1, decayed
    assert repo.get(stale.id) is None
    assert repo.get(ok.id) is not None


def test_decay_high_confidence_alone_does_not_grant_immunity():
    """The exact regression this rework targets: a self-rated 'high'
    confidence memory with zero corroboration and zero usage is NOT
    special-cased anymore — it decays on the same schedule as 'medium'."""
    repo = _fresh_repo()
    item = MemoryItem(content="important-sounding", description="an old 'high confidence' fact",
                       plane="world", type="reference", scope="project", confidence="high")
    item.created = "2020-01-01T00:00:00+00:00"
    repo.upsert(item)

    decayed = C.decay_unused_world_memories(repo, max_unused_days=90)
    assert decayed == 1, decayed
    assert repo.get(item.id) is None


def test_decay_corroboration_extends_grace_period():
    """corroboration_count — a deterministic, code-computed signal (see
    ops/extractor.py's duplicate_of handling), not a self-report — is the
    only thing allowed to buy a memory extra life."""
    repo = _fresh_repo()
    corroborated = MemoryItem(content="reaffirmed", description="a fact seen again independently",
                               plane="world", type="reference", scope="project", confidence="medium",
                               corroboration_count=2)
    corroborated.created = _iso_days_ago(200)  # > base 90d, but grace = 90 * min(1+2,6) = 270d
    repo.upsert(corroborated)

    decayed = C.decay_unused_world_memories(repo, max_unused_days=90)
    assert decayed == 0, decayed
    assert repo.get(corroborated.id) is not None


def test_decay_previously_used_memory_eventually_goes_stale():
    """Closes the old 'retrieved once, immortal forever' gap: usage_count>0
    still decays once long enough has passed since last_used."""
    repo = _fresh_repo()
    item = MemoryItem(content="once useful", description="was retrieved a long time ago",
                       plane="world", type="reference", scope="project", confidence="medium",
                       usage_count=1)
    item.created = _iso_days_ago(1000)
    item.last_used = _iso_days_ago(1000)  # grace(90) * STALE_USED_MULTIPLIER(3) = 270d — well past
    repo.upsert(item)

    decayed = C.decay_unused_world_memories(repo, max_unused_days=90)
    assert decayed == 1, decayed
    assert repo.get(item.id) is None


def test_decay_recently_used_memory_survives():
    repo = _fresh_repo()
    item = MemoryItem(content="still useful", description="was retrieved recently",
                       plane="world", type="reference", scope="project", confidence="medium",
                       usage_count=1)
    item.created = _iso_days_ago(1000)
    item.last_used = _iso_days_ago(5)
    repo.upsert(item)

    decayed = C.decay_unused_world_memories(repo, max_unused_days=90)
    assert decayed == 0, decayed
    assert repo.get(item.id) is not None


def test_decay_expires_volatile_memory_past_its_ttl_regardless_of_usage():
    """Volatile facts are time-bound by design (design doc §10) — ttl
    expiry applies even to a memory with usage_count>0, since there's no
    re-verify-on-read mechanism yet to keep it honestly alive."""
    repo = _fresh_repo()
    item = MemoryItem(content="sprint ends March 5", description="current sprint deadline",
                       plane="world", type="project", scope="project", confidence="high",
                       volatile=True, ttl_days=30, usage_count=5)
    item.created = _iso_days_ago(40)
    item.last_used = _iso_days_ago(1)
    repo.upsert(item)

    decayed = C.decay_unused_world_memories(repo, max_unused_days=90)
    assert decayed == 1, decayed
    assert repo.get(item.id) is None


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
