"""
Sanity checks for Memory Layer 2a — the unified end-of-session pass that
now handles BOTH agent-nominated candidates (from mid-session `remember`
calls, which do no I/O at call time — see src/core_tools/memory_tools.py)
and discovered candidates, in one pipeline (with a fake OpenAI client,
no network), plus the agent-driven consolidation judge (deterministic
volatile-TTL expiry + plan-based merge/delete apply) — the old jaccard
dedupe / self-rated-confidence heuristic decay gate is gone.

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
    prompt = build_extraction_user_prompt("transcript", nominated, [], other)
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
# Extraction checkpoint (memory/store.py's get/set_extraction_checkpoint) —
# run_extraction() only scans the messages added since the LAST pass for a
# given conversation_id, since _schedule_memory_distillation fires after
# EVERY turn (gateway/streaming.py), not once at the end.
# ---------------------------------------------------------------------------

def test_run_extraction_only_rescans_new_messages_on_a_later_turn():
    """Turn 1 nominates and writes a memory. Turn 2 adds two throwaway
    messages with no nomination of its own. If the checkpoint weren't
    advancing, turn 2 would re-send the ENTIRE (turn 1 + turn 2) transcript
    — which still contains turn 1's `remember` call — back to the model.
    With the checkpoint, turn 2's own new slice is just 2 plain messages:
    below MIN_MESSAGES_TO_BOTHER and with nothing nominated, so the model
    must not be called a second time at all."""
    cid = "conv-incremental"
    payload_turn1 = json.dumps({"memories": [{
        "plane": "stance", "type": "preference", "scope": "user", "source": "nominated",
        "content": _REMEMBER_ARGS["content"], "description": _REMEMBER_ARGS["description"], "confidence": "high",
    }]})
    _patch_extractor(payload_turn1)
    result1 = extractor.run_extraction(cid, _MSGS_WITH_REMEMBER_CALL)
    assert len(result1) == 1, result1

    model_calls = []

    def _explode_if_called(**kwargs):
        model_calls.append(kwargs)
        raise AssertionError("model must not be called again for turn 2's trivial new slice")

    extractor.OpenAI = lambda base_url, api_key: types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=_explode_if_called))
    )

    full_history_turn2 = _MSGS_WITH_REMEMBER_CALL + [
        {"role": "user", "content": "Thanks — one more unrelated question."},
        {"role": "assistant", "content": "Sure, go ahead."},
    ]
    result2 = extractor.run_extraction(cid, full_history_turn2)
    assert result2 == [], result2
    assert model_calls == [], "checkpoint did not prevent re-scanning turn 1's messages"


def test_run_extraction_checkpoint_advances_even_when_nothing_is_written():
    """A turn whose new content is judged and yields zero candidates must
    still advance the checkpoint — otherwise every all-no-op turn would
    keep growing the re-scanned window forever."""
    cid = "conv-checkpoint-noop"
    repo = extractor.get_repository()
    _patch_extractor('{"memories": []}')
    result = extractor.run_extraction(cid, _MSGS_WITH_REMEMBER_CALL)
    assert result == []
    assert repo.get_extraction_checkpoint(cid) == len(_MSGS_WITH_REMEMBER_CALL)


def test_run_extraction_checkpoint_not_advanced_when_extraction_disabled():
    """A 'never even tried' skip (extraction toggled off) must NOT burn the
    checkpoint — otherwise re-enabling passive extraction later would
    silently and permanently drop whatever accumulated while it was off."""
    cid = "conv-checkpoint-disabled"
    repo = extractor.get_repository()
    orig = extractor.passive_extraction_enabled
    extractor.passive_extraction_enabled = lambda: False
    try:
        result = extractor.run_extraction(cid, _MSGS_WITH_REMEMBER_CALL)
    finally:
        extractor.passive_extraction_enabled = orig
    assert result == []
    assert repo.get_extraction_checkpoint(cid) == 0


def test_run_extraction_checkpoint_not_advanced_when_no_provider_configured():
    """Same 'never even tried' reasoning as above, for the other upfront
    skip: no provider configured yet."""
    cid = "conv-checkpoint-unconfigured"
    repo = extractor.get_repository()
    extractor.get_memory_extraction_config = lambda: {
        "provider_id": "", "base_url": "", "api_key": "", "model": "",
    }
    result = extractor.run_extraction(cid, _MSGS_WITH_REMEMBER_CALL)
    assert result == []
    assert repo.get_extraction_checkpoint(cid) == 0


def test_extraction_checkpoint_is_reserved_before_the_slow_llm_call():
    """Regression test for a real race: gateway/streaming.py submits
    run_extraction to a background thread pool rather than awaiting it, so
    a resend that cancels+replaces the in-flight stream for a conversation
    (see _cancel_active_stream's "no stream ever outlives a cancel call"
    guarantee) can end up with the OLD stream's extraction call still
    waiting on a slow LLM response when the NEW stream finishes and
    submits its OWN extraction call for the SAME conversation_id. This
    simulates that reentrant second call happening WHILE the first call's
    model request is "in flight" (deterministic, no real threads needed —
    the fake client just calls back into run_extraction from inside its
    own create()). The reentrant call must see an already-advanced
    checkpoint and find nothing left to scan, proving the checkpoint is
    reserved before, not after, the network call."""
    cid = "conv-race"
    reentrant_results = []

    def _reentrant_create(**kwargs):
        reentrant_results.append(extractor.run_extraction(cid, _MSGS_WITH_REMEMBER_CALL))
        choice = types.SimpleNamespace(message=types.SimpleNamespace(content='{"memories": []}'))
        return types.SimpleNamespace(choices=[choice])

    extractor.get_memory_extraction_config = lambda: {
        "provider_id": "fake", "base_url": "http://fake", "api_key": "fake-key", "model": "fake-model",
    }
    extractor.OpenAI = lambda base_url, api_key: types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=_reentrant_create))
    )

    extractor.run_extraction(cid, _MSGS_WITH_REMEMBER_CALL)

    assert reentrant_results == [[]], reentrant_results


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


# ---------------------------------------------------------------------------
# Gap-investigation findings (`report_findings`, from an isolated Layer 2b
# worker transcript) — nominated exactly like `remember`, through the same
# gate. See ops/extractor.py module docstring "Layer 2b reuses this same
# gate" and memory/ops/dispatcher.py.
# ---------------------------------------------------------------------------

_REPORT_FINDINGS_ARGS_RESOLVED = {
    "resolved": True,
    "answer": "Pipeline bugs are tracked in the Linear INGEST project.",
    "description": "Where pipeline bugs are tracked",
    "confidence": "high",
}

_MSGS_WITH_REPORT_FINDINGS_CALL = [
    {"role": "user", "content": "Investigate this: where are pipeline bugs tracked?"},
    {
        "role": "assistant", "content": "Found it.",
        "tool_calls": [{
            "id": "call_1",
            "function": {"name": "report_findings", "arguments": json.dumps(_REPORT_FINDINGS_ARGS_RESOLVED)},
        }],
    },
    {"role": "tool", "content": "Findings recorded", "tool_call_id": "call_1"},
]


def test_report_findings_nominated_when_resolved():
    nominated = extractor._extract_nominated_candidates(_MSGS_WITH_REPORT_FINDINGS_CALL)
    assert len(nominated) == 1
    assert nominated[0]["content"] == _REPORT_FINDINGS_ARGS_RESOLVED["answer"]
    assert nominated[0]["type"] == "gap_resolution", "default type for report_findings must be gap_resolution"


def test_report_findings_not_nominated_when_unresolved():
    unresolved_msgs = [{
        "role": "assistant", "content": "",
        "tool_calls": [{
            "id": "c1",
            "function": {"name": "report_findings", "arguments": json.dumps({"resolved": False, "notes": "no evidence found"})},
        }],
    }]
    assert extractor._extract_nominated_candidates(unresolved_msgs) == [], \
        "an honest 'could not resolve' must never become a memory candidate"


def test_report_findings_missing_answer_is_skipped_defensively():
    bad_msgs = [{
        "role": "assistant", "content": "",
        "tool_calls": [{
            "id": "c1",
            "function": {"name": "report_findings", "arguments": json.dumps({"resolved": True, "description": "x"})},
        }],
    }]
    assert extractor._extract_nominated_candidates(bad_msgs) == []


def test_report_findings_multiple_calls_only_last_one_wins():
    """The investigator is told to call this exactly once, but if it
    disobeys, only its FINAL answer should be nominated — not two
    competing candidates for the same gap."""
    msgs = [{
        "role": "assistant", "content": "",
        "tool_calls": [
            {"id": "c1", "function": {"name": "report_findings", "arguments": json.dumps({
                "resolved": True, "answer": "first guess", "description": "first"})}},
            {"id": "c2", "function": {"name": "report_findings", "arguments": json.dumps({
                "resolved": True, "answer": "final answer", "description": "final"})}},
        ],
    }]
    nominated = extractor._extract_nominated_candidates(msgs)
    assert len(nominated) == 1
    assert nominated[0]["content"] == "final answer"


def test_run_gap_investigation_extraction_writes_and_returns_id_and_confidence():
    payload = json.dumps({"memories": [{
        "plane": "world", "type": "gap_resolution", "scope": "project", "source": "nominated",
        "content": _REPORT_FINDINGS_ARGS_RESOLVED["answer"],
        "description": _REPORT_FINDINGS_ARGS_RESOLVED["description"],
        "confidence": "high",
    }]})
    _patch_extractor(payload)
    result = extractor.run_gap_investigation_extraction("gap_abc123", _MSGS_WITH_REPORT_FINDINGS_CALL)
    assert result is not None
    memory_id, confidence = result
    saved = extractor.get_repository().get(memory_id)
    assert saved is not None and saved.content == _REPORT_FINDINGS_ARGS_RESOLVED["answer"]
    assert confidence == "high"
    assert "report_findings" in saved.provenance


def test_run_gap_investigation_extraction_returns_none_when_rejected():
    _patch_extractor('{"memories": []}')
    result = extractor.run_gap_investigation_extraction("gap_reject", _MSGS_WITH_REPORT_FINDINGS_CALL)
    assert result is None


def test_run_gap_investigation_extraction_returns_none_when_nothing_to_nominate():
    """No report_findings call (or an unresolved one) with an otherwise
    trivial transcript must short-circuit before even calling the model —
    same MIN_MESSAGES_TO_BOTHER gate as the normal path."""
    calls = []
    extractor.get_memory_extraction_config = lambda: {
        "provider_id": "fake", "base_url": "http://fake", "api_key": "fake-key", "model": "fake-model",
    }
    extractor.OpenAI = lambda base_url, api_key: calls.append(1) or _FakeClient('{"memories":[]}')
    result = extractor.run_gap_investigation_extraction("gap_empty", [{"role": "user", "content": "hi"}])
    assert result is None and not calls


def test_report_findings_tool_is_a_pure_local_noop():
    """Mirrors remember_tool: must never touch the network, only leaves a
    transcript marker for the dispatcher/extractor to read afterward."""
    import requests
    from src.core_tools import memory_tools

    def _fail_if_called(*a, **kw):
        raise AssertionError("report_findings_tool must not perform any HTTP call")

    orig_get, orig_post = requests.get, requests.post
    requests.get, requests.post = _fail_if_called, _fail_if_called
    try:
        msg, echoed = memory_tools.report_findings_tool(_REPORT_FINDINGS_ARGS_RESOLVED)
        msg2, echoed2 = memory_tools.report_findings_tool({"resolved": False, "notes": "nothing found"})
    finally:
        requests.get, requests.post = orig_get, orig_post

    assert "recorded" in msg.lower()
    assert echoed is _REPORT_FINDINGS_ARGS_RESOLVED
    assert "could not resolve" in msg2.lower()
    assert "nothing found" in msg2


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


# ---------------------------------------------------------------------------
# Consolidation is now agent-driven (the judge gets the whole world corpus and
# decides merges/deletes semantically), so the old jaccard dedupe + self-rated
# confidence / grace-day heuristic decay tests are gone. What's still pure and
# worth covering: the deterministic volatile-TTL expiry pass, and the plan-
# application that the judge's output flows through (merge keeps retention
# history; delete retires world but never stance; unknown ids are skipped).
# The LLM judgment call itself is exercised end-to-end in run_consolidation
# with a fake client returning an empty plan below.
# ---------------------------------------------------------------------------

def test_expire_volatile_drops_world_memory_past_its_ttl_regardless_of_usage():
    """Volatile facts are time-bound by design (design doc §10) — ttl expiry
    is the one deterministic code pass retained; it applies even with
    usage_count>0 since there's no re-verify-on-read yet."""
    repo = _fresh_repo()
    item = MemoryItem(content="sprint ends March 5", description="current deadline",
                       plane="world", type="project", scope="project", confidence="high",
                       volatile=True, ttl_days=30, usage_count=5)
    item.created = _iso_days_ago(40)
    item.last_used = _iso_days_ago(1)
    repo.upsert(item)

    removed = C._expire_volatile(repo)
    assert removed == 1, removed
    assert repo.get(item.id) is None


def test_expire_volatile_keeps_within_ttl():
    repo = _fresh_repo()
    item = MemoryItem(content="sprint ends March 5", description="current deadline",
                       plane="world", type="project", scope="project", confidence="high",
                       volatile=True, ttl_days=30, usage_count=0)
    item.created = _iso_days_ago(5)
    repo.upsert(item)
    assert C._expire_volatile(repo) == 0
    assert repo.get(item.id) is not None


def test_expire_volatile_never_touches_stance_plane():
    repo = _fresh_repo()
    stance = MemoryItem(content="pref", description="a stance pref", plane="stance",
                         type="preference", scope="user", confidence="low", volatile=True, ttl_days=1)
    stance.created = _iso_days_ago(100)
    repo.upsert(stance)
    assert C._expire_volatile(repo) == 0  # stance excluded from the corpus
    assert repo.get(stance.id) is not None


def test_apply_plan_merge_folders_loser_into_keeper_and_preserves_history():
    """A judge merge keeps the larger usage, oldest-created, and bumps
    corroboration (loser independently resolved to the same fact) — it must
    NOT reset the retention history decay/retention judges by."""
    repo = _fresh_repo()
    keeper = MemoryItem(content="keeper-body", description="Pipeline bugs in Linear",
                          plane="world", type="reference", scope="project", confidence="medium",
                          usage_count=5, corroboration_count=1)
    keeper.created = _iso_days_ago(100)
    keeper.last_used = _iso_days_ago(2)
    loser = MemoryItem(content="loser-body", description="pipeline bugs tracked in linear",
                        plane="world", type="reference", scope="project", confidence="low",
                        usage_count=2, corroboration_count=3)
    loser.created = _iso_days_ago(40)
    loser.last_used = _iso_days_ago(50)
    repo.upsert(keeper)
    repo.upsert(loser)

    plan = {"merges": [{"into": keeper.id, "from": loser.id,
                          "content": "combined body", "description": "combined summary",
                          "confidence": "high"}], "deletes": []}
    res = C._apply_plan(repo, plan)
    assert res == {"merged": 1, "deleted": 0}, res

    merged = repo.get(keeper.id)
    assert repo.get(loser.id) is None  # loser removed
    assert merged.content == "combined body"
    assert merged.description == "combined summary"
    assert merged.confidence == "high"
    assert merged.usage_count == 5  # max, not reset
    assert merged.corroboration_count == 1 + 3 + 1  # bumped
    assert merged.supersedes == loser.id


def test_apply_plan_delete_retires_world_but_never_stance():
    repo = _fresh_repo()
    world = MemoryItem(content="w", description="d", plane="world", type="reference",
                        scope="project", confidence="low")
    stance = MemoryItem(content="s", description="sp", plane="stance", type="preference",
                         scope="user", confidence="low")
    repo.upsert(world)
    repo.upsert(stance)
    # Judge only ever sees world items; an id it invented / a stance id it
    # somehow emitted must be skipped, not honored.
    plan = {"merges": [], "deletes": [world.id, stance.id, "does-not-exist"]}
    res = C._apply_plan(repo, plan)
    assert res == {"merged": 0, "deleted": 1}, res
    assert repo.get(world.id) is None
    assert repo.get(stance.id) is not None  # stance never auto-removed


def test_apply_plan_skips_unknown_and_self_merging_ids():
    repo = _fresh_repo()
    a = MemoryItem(content="a", description="da", plane="world", type="reference",
                    scope="project", confidence="low")
    repo.upsert(a)
    plan = {"merges": [{"into": "nope", "from": a.id}, {"into": a.id, "from": a.id}],
             "deletes": []}
    assert C._apply_plan(repo, plan) == {"merged": 0, "deleted": 0}
    assert repo.get(a.id) is not None


def test_run_consolidation_with_empty_plan_is_fail_open_noop():
    """End-to-end: provider is faked, the judge returns an empty plan, and
    volatile expiry still runs as the deterministic pre-pass."""
    # Consolidator uses its own binding of get_memory_extraction_config
    # (imported at module load), so patch C's, not extractor's.
    C.get_memory_extraction_config = lambda: {
        "provider_id": "fake", "base_url": "http://fake", "api_key": "fake-key", "model": "fake-model",
    }
    C.OpenAI = lambda base_url, api_key: _FakeClient('{"merges": [], "deletes": []}')
    repo = _fresh_repo()
    # Two world memories: one volatile + past ttl (expired by the code
    # pre-pass), one stable (survives — so the judge is actually called with
    # a non-empty corpus and returns the canned empty plan, exercising the
    # LLM path + apply, not just the expiry short-circuit).
    volatile = MemoryItem(content="v", description="v", plane="world", type="project",
                           scope="project", confidence="high", volatile=True, ttl_days=10)
    volatile.created = _iso_days_ago(20)
    repo.upsert(volatile)
    stable = MemoryItem(content="stable", description="a stable fact", plane="world",
                        type="reference", scope="project", confidence="medium")
    stable.created = _iso_days_ago(5)
    repo.upsert(stable)
    # consolidator imported get_repository into its own namespace at module
    # load (``from memory.store import ..., get_repository``), so patch C's
    # binding, not memory.store's — otherwise run_consolidation still sees
    # the real global repo and never the temp corpus we just seeded.
    orig = C.get_repository
    C.get_repository = lambda: repo
    try:
        res = C.run_consolidation()
    finally:
        C.get_repository = orig
    assert res["expired"] == 1, res  # volatile expired by code pass
    assert res["merged"] == 0 and res["deleted"] == 0, res  # judge no-op applied cleanly


def _run_all():
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"OK  {t.__name__}")
    print(f"\nALL {len(tests)} LAYER 2 CHECKS PASSED")


if __name__ == "__main__":
    _run_all()
