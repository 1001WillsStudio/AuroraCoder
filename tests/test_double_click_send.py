"""Regression: double-clicking Send must not abort the in-flight reply.

Explorer (fresh page load, desktop): type a prompt, double-click the
paper-plane. The first click starts POST /api/chat; the second either
re-enters handleSend (isStreaming is still false — the first await is
getActiveStreams) or lands on the Stop control after React swaps the
button. Either path aborts the fetch (AbortError: signal is aborted
without reason) and leaves a user bubble plus an empty assistant.

Pressing Enter twice quickly is the same re-entry: two POSTs without a
conversation_id, so History gains two chats with the same title.

There is no JS test runner here, so behaviour is locked two ways:
  * helpers in frontend/src/utils/composerGuard.js run under Node
    (hermetic: no network, no DOM);
  * a source scan asserts handleSend claims the lock before the first
    await, and ChatInput ignores click.detail > 1 on Send and Stop.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "frontend" / "src" / "utils" / "composerGuard.js"
APP = ROOT / "frontend" / "src" / "App.jsx"
CHAT_INPUT = ROOT / "frontend" / "src" / "components" / "ChatInput.jsx"


def _handle_send_src() -> str:
    src = APP.read_text(encoding="utf-8")
    return src.split("const handleSend =", 1)[1].split("const handleInterruptSend", 1)[0]


def _eval_js(expr: str):
    script = (
        "import {\n"
        "  isPrimaryClick,\n"
        "  shouldBeginSend,\n"
        "  shouldAcceptStop,\n"
        "  createSendLock,\n"
        "  STOP_ARM_MS,\n"
        f"}} from {json.dumps(HELPER.resolve().as_uri())}\n"
        f"const out = {expr}\n"
        "console.log(JSON.stringify(out))\n"
    )
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout or "node helper failed")
    return json.loads(proc.stdout)


@pytest.mark.unit
def test_second_send_before_streaming_state_is_ignored():
    """Reproduce: two handleSend calls before React sets isStreaming.

    The first click/Enter claims the lock; the second must not start
    another /api/chat (that second call used to abort the first).
    """
    accepted = _eval_js(
        """(() => {
          const lock = createSendLock();
          const first = lock.begin(false);
          const second = lock.begin(false);
          return { firstOk: first != null, secondOk: second != null, inFlight: lock.inFlight };
        })()"""
    )
    assert accepted["firstOk"] is True
    assert accepted["secondOk"] is False
    assert accepted["inFlight"] is True


@pytest.mark.unit
def test_double_click_detail_does_not_fire_stop():
    """Reproduce: after Send becomes Stop, the second click has detail 2.

    That click must not abort the request that the first click started.
    """
    assert _eval_js("isPrimaryClick({ detail: 1 })") is True
    assert _eval_js("isPrimaryClick({ detail: 2 })") is False
    assert _eval_js("isPrimaryClick({ detail: 3 })") is False
    assert _eval_js("isPrimaryClick({})") is True
    assert _eval_js("isPrimaryClick(null)") is True


@pytest.mark.unit
def test_interrupt_can_reenter_while_in_flight():
    """A deliberate interrupt still replaces the in-flight turn."""
    out = _eval_js(
        """(() => {
          const lock = createSendLock();
          const first = lock.begin(false);
          const interrupt = lock.begin(true);
          lock.end(first);
          return {
            interruptOk: interrupt != null,
            stillInFlight: lock.inFlight,
          };
        })()"""
    )
    assert out["interruptOk"] is True
    # Predecessor end() must not clear the interrupt's claim.
    assert out["stillInFlight"] is True
    assert _eval_js("shouldBeginSend(true, false)") is False
    assert _eval_js("shouldBeginSend(true, true)") is True
    assert _eval_js("shouldBeginSend(false, false)") is True


@pytest.mark.unit
def test_stop_ignored_during_double_click_window():
    """If Send is replaced by Stop, the second click may have detail 1
    on the new node. Stop must still no-op for a short arming window."""
    assert _eval_js("shouldAcceptStop(1000, 1100, 500)") is False
    assert _eval_js("shouldAcceptStop(1000, 1500, 500)") is True
    assert _eval_js("shouldAcceptStop(0, 1000, 500)") is True
    assert _eval_js("STOP_ARM_MS") >= 500


@pytest.mark.unit
def test_lock_releases_only_matching_token():
    out = _eval_js(
        """(() => {
          const lock = createSendLock();
          const first = lock.begin(false);
          lock.end(first);
          return { inFlight: lock.inFlight, canSendAgain: lock.begin(false) != null };
        })()"""
    )
    assert out["inFlight"] is False
    assert out["canSendAgain"] is True


@pytest.mark.unit
def test_handle_send_claims_lock_before_get_active_streams():
    """The first await on a fresh chat is getActiveStreams; the lock
    must already be held or two Enters both create a conversation."""
    send = _handle_send_src()
    app = APP.read_text(encoding="utf-8")
    assert "createSendLock" in app
    assert "sendLockRef" in send
    lock_at = send.find("sendLockRef.current.begin")
    await_at = send.find("getActiveStreams")
    assert lock_at != -1 and await_at != -1
    assert lock_at < await_at, (
        "send lock must be claimed before getActiveStreams, otherwise a "
        "second Enter/click starts another POST /api/chat and aborts the first"
    )
    stop = APP.read_text(encoding="utf-8")
    stop = stop.split("const handleStop =", 1)[1].split("const handleStopTool", 1)[0]
    assert "shouldAcceptStop" in stop


@pytest.mark.unit
def test_chat_input_ignores_non_primary_clicks_on_send_and_stop():
    """Send swapping to Stop is the double-click target; both must
    ignore event.detail > 1."""
    src = CHAT_INPUT.read_text(encoding="utf-8")
    assert src.count("isPrimaryClick") >= 3
    assert "isPrimaryClick(e)) return; onSend()" in src
    assert "isPrimaryClick(e)) return; onStop()" in src
