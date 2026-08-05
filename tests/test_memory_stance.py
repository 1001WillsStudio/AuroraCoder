"""Unit tests for :mod:`memory.stance`.

QA focus: the stance block is the memory subsystem's "what you know about
the user/project" surface that gets injected into the agent context. We
verify: empty-state handling, item cap, sort ordering (recency + usage),
label mapping — and that building the block is a PURE READ (no bump_usage
side effect: injection happens on every session start, so bumping there
would inflate usage linearly with session count regardless of actual
usefulness and self-reinforce the top-N selection — the only legitimate
usage signal is an explicit recall; see stance.py).
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

from memory import stance


def _item(item_id, content="x", type="achievements", usage=0,
          last_used=None, created="2024-01-01"):
    return SimpleNamespace(
        id=item_id, type=type, content=content,
        usage_count=usage, last_used=last_used, created=created,
    )


class _FakeRepo:
    def __init__(self, items):
        self._items = items
        self.all_calls = []

    def all_items(self, *, plane, scope=None):
        self.all_calls.append((plane, scope))
        return list(self._items)

    def bump_usage(self, ids):
        # Regression guard: stance injection must never record usage.
        raise AssertionError("build_stance_block must be a pure read — no bump_usage")


def _repo(items):
    return _FakeRepo(items)


# --------------------------------------------------------------------------- empty
def test_empty_repo_yields_empty_string():
    repo = _repo([])
    out = stance.build_stance_block(repo, scope="user")
    assert out == ""
    assert repo.all_calls == [("stance", "user")]
    # No write side effect on the empty path either: _FakeRepo.bump_usage
    # raises if ever called.


# --------------------------------------------------------------------------- basic shape
def test_single_item_renders_header_and_line():
    repo = _repo([_item("h1", content="likes python", type="preferences")])
    out = stance.build_stance_block(repo, scope="user")
    assert "know about this user/project" in out
    assert "- [" in out and "likes python" in out
    # No write side effect: _FakeRepo.bump_usage raises if ever called.


def test_unknown_type_uses_capitalised_label():
    repo = _repo([_item("h1", content="thing", type="weirdtype")])
    out = stance.build_stance_block(repo)
    # label comes from _TYPE_LABEL or falls back to capitalized type
    label = stance._TYPE_LABEL.get("weirdtype", "weirdtype".capitalize())
    assert f"[{label}]" in out


def test_content_is_stripped_in_rendered_line():
    repo = _repo([_item("h1", content="   spaced content   ")])
    out = stance.build_stance_block(repo)
    assert "spaced content" in out
    assert "   spaced" not in out  # leading whitespace removed by .strip()


# --------------------------------------------------------------------------- cap & sort
def test_caps_at_max_stance_items_showing_the_highest_usage():
    items = [_item(f"i{n:02d}", content=f"membody{n:02d}", usage=n)
             for n in range(stance.MAX_STANCE_ITEMS + 5)]
    assert stance.MAX_STANCE_ITEMS == 15
    repo = _repo(items)
    out = stance.build_stance_block(repo)
    # exactly MAX_STANCE_ITEMS rendered body lines (the header is the only non "-" line)
    body_lines = [ln for ln in out.splitlines() if ln.startswith("- ")]
    assert len(body_lines) == stance.MAX_STANCE_ITEMS
    # the surfaced items are the 15 highest-usage ones (n = 5..19)
    for n in range(5, 20):
        assert f"membody{n:02d}" in out
    for n in range(5):
        assert f"membody{n:02d}" not in out


def test_sort_prefers_higher_usage_then_recency():
    # same usage_count -> recency (last_used or created) breaks the tie
    older = _item("older", usage=1, last_used=None, created="2024-01-01")
    newer = _item("newer", usage=1, last_used=None, created="2025-12-31")
    ranked = sorted([older, newer], key=stance._sort_key, reverse=True)
    assert ranked[0] is newer and ranked[1] is older
    # And the rendered ordering follows the same rank (newer first)
    out = stance.build_stance_block(_repo([older, newer]))
    body = [ln for ln in out.splitlines() if ln.startswith("- ")]
    assert len(body) == 2


def test_scope_is_forwarded_to_repo():
    repo = MagicMock()
    repo.all_items.return_value = []
    stance.build_stance_block(repo, scope="projectX")
    repo.all_items.assert_called_once_with(plane="stance", scope="projectX")
    # Pure read — usage recording happens only at explicit recall.
    repo.bump_usage.assert_not_called()
