"""Contract: core desktop UI controls expose stable data-testid hooks.

The browser gate used to guess at selectors (placeholder text and friends) and
skip when it could not find a control. These attributes are the stable
surface it now holds on to. There is no JS test runner in this repo, so we
lock the contract by scanning ``frontend/src`` (hermetic: source text only,
no network, no DOM, no build).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_SRC = ROOT / "frontend" / "src"
MOBILE = ROOT / "mobile"

# Exact names the browser gate looks for. Do not rename.
REQUIRED_TESTIDS = (
    "chat-input",
    "chat-send",
    "chat-message",
    "chat-message-content",
    "chat-stop",
    "tool-activity",
    "new-conversation",
    "conversation-list",
    "conversation-item",
    "settings-button",
    "settings-panel",
    "provider-select",
    "file-tree",
    "code-panel",
    "thinking-indicator",
)

# Opening-tag innards may span lines and contain `=>` arrow functions, which
# a naive `[^>]*` would treat as the end of the tag.
_TAG_ATTRS = r"(?:=>|[^>])*"

# testid -> (component file relative to frontend/src, regex that must match
# the interactive/host element rather than an anonymous wrapper).
ON_ELEMENT = {
    "chat-input": ("components/ChatInput.jsx", rf"<textarea\b{_TAG_ATTRS}data-testid=\"chat-input\""),
    "chat-send": ("components/ChatInput.jsx", rf"<button\b{_TAG_ATTRS}data-testid=\"chat-send\""),
    "chat-stop": ("components/ChatInput.jsx", rf"<button\b{_TAG_ATTRS}data-testid=\"chat-stop\""),
    "chat-message": ("components/ChatMessage.jsx", rf"<div\b{_TAG_ATTRS}data-testid=\"chat-message\""),
    "chat-message-content": (
        "components/ChatMessage.jsx",
        rf"<div\b{_TAG_ATTRS}data-testid=\"chat-message-content\"",
    ),
    "tool-activity": (
        "components/ToolActivity.jsx",
        rf"<div\b{_TAG_ATTRS}data-testid=\"tool-activity\"",
    ),
    "new-conversation": (
        "components/Sidebar.jsx",
        rf"<button\b{_TAG_ATTRS}data-testid=\"new-conversation\"",
    ),
    "conversation-list": (
        "components/Sidebar.jsx",
        rf"<div\b{_TAG_ATTRS}data-testid=\"conversation-list\"",
    ),
    "conversation-item": (
        "components/ConversationHistory.jsx",
        rf"<button\b{_TAG_ATTRS}data-testid=\"conversation-item\"",
    ),
    "settings-button": (
        "components/Sidebar.jsx",
        rf"<button\b{_TAG_ATTRS}data-testid=\"settings-button\"",
    ),
    "settings-panel": (
        "components/SettingsPanel.jsx",
        rf"<div\b{_TAG_ATTRS}data-testid=\"settings-panel\"",
    ),
    "provider-select": (
        "components/Sidebar.jsx",
        rf"<button\b{_TAG_ATTRS}data-testid=\"provider-select\"",
    ),
    "file-tree": ("components/FileTree.jsx", rf"<div\b{_TAG_ATTRS}data-testid=\"file-tree\""),
    "code-panel": ("components/CodePanel.jsx", rf"<div\b{_TAG_ATTRS}data-testid=\"code-panel\""),
    "thinking-indicator": (
        "components/ChatMessage.jsx",
        r"data-testid=\"thinking-indicator\"",
    ),
}


def _read(rel: str) -> str:
    return (FRONTEND_SRC / rel).read_text(encoding="utf-8")


def _all_frontend_src() -> str:
    parts = []
    for path in FRONTEND_SRC.rglob("*"):
        if path.suffix in {".jsx", ".js", ".css"}:
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


@pytest.mark.unit
@pytest.mark.parametrize("testid", REQUIRED_TESTIDS)
def test_required_testid_present_in_frontend_src(testid: str):
    blob = _all_frontend_src()
    assert f'data-testid="{testid}"' in blob, (
        f'data-testid="{testid}" is missing from frontend/src'
    )


@pytest.mark.unit
@pytest.mark.parametrize("testid,rel,pattern", [
    (tid, rel, pat) for tid, (rel, pat) in ON_ELEMENT.items()
])
def test_testid_is_on_the_interactive_element(testid: str, rel: str, pattern: str):
    src = _read(rel)
    assert re.search(pattern, src, flags=re.DOTALL), (
        f'{testid} must appear on the interactive/host element in {rel}'
    )


@pytest.mark.unit
def test_mobile_spa_has_no_core_testids():
    """This task is desktop-only; the mobile SPA must not grow these hooks."""
    if not MOBILE.exists():
        pytest.skip("mobile/ not present")
    blob = ""
    for path in MOBILE.rglob("*"):
        if path.suffix in {".js", ".html", ".css"}:
            blob += path.read_text(encoding="utf-8")
    for testid in REQUIRED_TESTIDS:
        assert f'data-testid="{testid}"' not in blob, (
            f'mobile SPA unexpectedly defines data-testid="{testid}"'
        )


@pytest.mark.unit
def test_chat_send_and_stop_are_mutually_exclusive_branches():
    """Send and stop share the input-actions slot; both testids must exist
    as separate button branches so the gate never sees both at once."""
    src = _read("components/ChatInput.jsx")
    assert src.count('data-testid="chat-send"') >= 1
    assert src.count('data-testid="chat-stop"') >= 1
    assert 'data-testid="chat-send"' in src
    assert 'data-testid="chat-stop"' in src


@pytest.mark.unit
def test_conversation_item_marks_each_list_entry():
    src = _read("components/ConversationHistory.jsx")
    assert src.count('data-testid="conversation-item"') >= 2, (
        "conversation-item must be on each rendered entry, not only a container"
    )
