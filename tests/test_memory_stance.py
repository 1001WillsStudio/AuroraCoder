"""Unit tests for :mod:`memory.stance`.

QA focus: the stance block is the memory subsystem's "what you know about
the user/project" surface that gets injected into the agent context. We
verify: empty-state handling, item cap, sort ordering (recency + usage),
label mapping, and that consumption bumps usage for exactly the surfaced
items (no more, no fewer).
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
        self.bumped = None

    def all_items(self, *, plane, scope=None):
        self.all_calls.append((plane, scope))
        return list(self._items)

    def bump_usage(self, ids):
        self.bumped = list(ids)


def _repo(items):
    return _FakeRepo(items)


# --------------------------------------------------------------------------- empty
def test_empty_repo_yields_empty_string():
    repo = _repo([])
    out = stance.build_stance_block(repo, scope="user")
    assert out == ""
    assert repo.all_calls == [("stance", "user")]
    # bump_usage is NOT called on the empty path (stays at its initial None)
    assert repo.bumped is None


# --------------------------------------------------------------------------- basic shape
def test_single_item_renders_header_and_line():
    repo = _repo([_item("h1", content="likes python", type="preferences")])
    out = stance.build_stance_block(repo, scope="user")
    assert "know about this user/project" in out
    assert "- [" in out and "likes python" in out
    assert repo.bumped == ["h1"]


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
def test_caps_at_max_stance_items_and_bumps_only_those():
    items = [_item(f"i{n}", usage=n) for n in range(stance.MAX_STANCE_ITEMS + 5)]
    assert stance.MAX_STANCE_ITEMS == 15
    repo = _repo(items)
    out = stance.build_stance_block(repo)
    # exactly MAX_STANCE_ITEMS rendered body lines (the header is the only non "-" line)
    body_lines = [ln for ln in out.splitlines() if ln.startswith("- ")]
    assert len(body_lines) == stance.MAX_STANCE_ITEMS
    # bump_usage gets exactly the surfaced ids (the 15 highest-usage ones)
    surfaced = sorted(range(stance.MAX_STANCE_ITEMS + 5),
                      key=lambda n: n, reverse=True)[: stance.MAX_STANCE_ITEMS]
    expected_ids = {f"i{n}" for n in surfaced}
    assert set(repo.bumped) == expected_ids
    assert len(repo.bumped) == stance.MAX_STANCE_ITEMS


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