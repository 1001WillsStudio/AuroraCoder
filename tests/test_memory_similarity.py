"""Unit tests for :mod:`memory.ops.similarity`.

QA focus: similarity scoring is the foundation of recall/consolidation
correctness. We lock down tokenization rules and the overlap metric,
including the degenerate (empty-token) cases, plus the
``find_similar_existing`` repository query.
"""
import pytest

from memory.ops import similarity


# --------------------------------------------------------------------------- tokens
def test_tokens_lowercases_and_filters_short_words():
    assert similarity.tokens("Hello World") == {"hello", "world"}
    # words shorter than 3 chars are dropped
    assert similarity.tokens("ab cd e f") == set()
    # non-alphanumeric acts as a delimiter
    assert similarity.tokens("one-two_three.four") == {"one", "two", "three", "four"}


def test_tokens_dedupes_and_strips_punctuation():
    toks = similarity.tokens("Run, run! RUN? running...")
    assert toks == {"run", "running"}
    # punctuation neighbours don't leak in
    assert all(all(c.isalnum() for c in t) for t in toks)


# --------------------------------------------------------------------------- similarity
def test_similarity_identical_strings_one():
    assert similarity.similarity("alpha beta gamma", "alpha beta gamma") == 1.0


def test_similarity_disjoint_strings_zero():
    assert similarity.similarity("alpha beta", "gamma delta") == 0.0


def test_similarity_empty_token_short_circuits_to_zero():
    assert similarity.similarity("", "alpha beta") == 0.0
    assert similarity.similarity("ab", "alpha beta") == 0.0  # tokens() empty
    assert similarity.similarity("alpha beta", "") == 0.0


def test_similarity_partial_overlap_value():
    # overlap: {"alpha"} ; union: {"alpha","beta","gamma"} -> 1/3
    val = similarity.similarity("alpha beta", "alpha gamma")
    assert val == pytest.approx(1 / 3)


# --------------------------------------------------------------------------- find_similar_existing
class _FakeRepo:
    """Minimal repo satisfying find_similar_existing's contract.

    ``find_similar_existing`` accesses rows with *dict* indexing
    (``row["description"]`` etc.), so rows must be dicts, not namespaces.
    It returns dicts carrying {id, description, type, confidence}.
    """

    def __init__(self, rows):
        self._rows = rows
        self.calls = []

    def list(self, *, plane=None, scope=None):
        self.calls.append((plane, scope))
        return [dict(r) for r in self._rows]


def test_find_similar_empty_description_returns_empty():
    repo = _FakeRepo([{"id": "1", "description": "alpha", "type": "fact", "confidence": 1.0}])
    assert similarity.find_similar_existing(repo, "stance", "user", "", limit=5) == []
    # still reaches repo.list (so callers can short-circuit themselves if wanted)
    assert repo.calls == []


def test_find_similar_filters_by_threshold_strictly():
    rows = [
        {"id": "a", "description": "alpha beta", "type": "fact", "confidence": 0.9},
        {"id": "b", "description": "alpha gamma", "type": "fact", "confidence": 0.5},
        {"id": "c", "description": "delta epsilon", "type": "fact", "confidence": 1.0},
    ]
    repo = _FakeRepo(rows)
    # description "alpha beta": vs "alpha gamma" overlap {alpha}/union3 = 0.333;
    # vs "alpha beta" = 1.0. Threshold 0.34 -> only identical kept.
    hits = similarity.find_similar_existing(repo, "stance", "user",
                                            "alpha beta", limit=5, threshold=0.34)
    assert [h["id"] for h in hits] == ["a"]
    # hit carries the descriptive fields forward (no extra 'score' key)
    assert hits[0]["description"] == "alpha beta"
    assert hits[0]["type"] == "fact"
    assert set(hits[0].keys()) == {"id", "description", "type", "confidence"}


def test_find_similar_threshold_inclusive_boundary():
    rows = [{"id": "z", "description": "alpha beta gamma", "type": "fact", "confidence": 1.0}]
    repo = _FakeRepo(rows)
    # exact match scores 1.0; threshold exactly 1.0 is NOT strictly greater -> excluded
    assert similarity.find_similar_existing(repo, "stance", "user",
                                            "alpha beta gamma", limit=5, threshold=1.0) == []
    # threshold just below 1.0 -> included
    assert (
        similarity.find_similar_existing(repo, "stance", "user",
                                          "alpha beta gamma", limit=5, threshold=0.99)
        != []
    )


def test_find_similar_honours_limit_and_orders_by_score_desc():
    rows = [
        {"id": "low", "description": "alpha", "type": "fact", "confidence": 0.3},
        {"id": "mid", "description": "alpha beta", "type": "fact", "confidence": 0.6},
        {"id": "hi", "description": "alpha beta gamma", "type": "fact", "confidence": 1.0},
    ]
    repo = _FakeRepo(rows)
    hits = similarity.find_similar_existing(repo, "stance", "user",
                                            "alpha beta gamma", limit=2, threshold=0.0)
    # limit=2 keeps the two highest-scoring, highest first
    assert [h["id"] for h in hits] == ["hi", "mid"]
    # order is purely by overlap score (highest first); the row dict carries confidence
    assert [h["confidence"] for h in hits] == [1.0, 0.6]


def test_find_similar_forwards_plane_and_scope():
    repo = _FakeRepo([])
    similarity.find_similar_existing(repo, "procedural", "projectA", "alpha beta")
    assert repo.calls == [("procedural", "projectA")]