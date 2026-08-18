"""Regression tests for conversation-fork UUID generation.

The fork button used to call ``crypto.randomUUID()`` directly. That method
is secure-context-only (HTTPS or localhost). Served over http:// on a
non-localhost hostname, ``typeof crypto.randomUUID === 'undefined'`` and
the click throws ``TypeError: crypto.randomUUID is not a function`` —
no conversation is created, no toast, no /api request.

These tests lock the fallback helper and the App.jsx call site so that
insecure-context crypto cannot throw.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP_JSX = ROOT / "frontend" / "src" / "App.jsx"
UUID_JS = ROOT / "frontend" / "src" / "utils" / "uuid.js"

UUID_V4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def test_fork_handler_does_not_call_crypto_random_uuid():
    """The original bug: handleForkConversation called crypto.randomUUID()."""
    source = APP_JSX.read_text(encoding="utf-8")
    assert "crypto.randomUUID" not in source
    assert "newConversationId(" in source
    assert "from './utils/uuid.js'" in source or 'from "./utils/uuid.js"' in source


def test_uuid_helper_guards_random_uuid_and_has_fallback():
    """The helper must not assume randomUUID exists."""
    source = UUID_JS.read_text(encoding="utf-8")
    assert "typeof c.randomUUID === 'function'" in source or (
        'typeof c.randomUUID === "function"' in source
    )
    assert "getRandomValues" in source
    assert "Math.random" in source
    assert "export function newConversationId" in source
    # RFC 4122 v4 nibble stamps — same constants the Node cases assert.
    assert "& 0x0f) | 0x40" in source
    assert "& 0x3f) | 0x80" in source


@pytest.fixture(scope="module")
def helper_results(tmp_path_factory):
    """Execute the real JS helper under Node with injected crypto stubs."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available to execute the frontend helper")

    tmp_path = tmp_path_factory.mktemp("uuid")
    runner = tmp_path / "run_uuid.mjs"
    helper_uri = UUID_JS.resolve().as_uri()
    runner.write_text(
        f"""
import {{ newConversationId }} from {helper_uri!r};

const UUID_RE = /^[0-9a-f]{{8}}-[0-9a-f]{{4}}-4[0-9a-f]{{3}}-[89ab][0-9a-f]{{3}}-[0-9a-f]{{12}}$/i;

function fillSequential(arr) {{
  for (let i = 0; i < arr.length; i++) arr[i] = i;
  return arr;
}}

const results = {{}};

// Reported failure: crypto exists, randomUUID is not a function.
const insecure = {{ getRandomValues: fillSequential }};
results.insecure = newConversationId(insecure);
results.insecureOk = UUID_RE.test(results.insecure);

// Deterministic bytes 0..15 → version/variant nibbles applied.
results.insecureExpected = '00010203-0405-4607-8809-0a0b0c0d0e0f';

// Prefer randomUUID when it really is a function (secure context).
const secure = {{
  randomUUID: () => 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee',
  getRandomValues: () => {{ throw new Error('getRandomValues should not run'); }},
}};
results.secure = newConversationId(secure);

// Last-resort: no Web Crypto at all (must not throw).
results.noCrypto = newConversationId(null);
results.noCryptoOk = UUID_RE.test(results.noCrypto);
results.noCrypto2 = newConversationId(null);
results.distinct = results.noCrypto !== results.noCrypto2;

console.log(JSON.stringify(results));
""",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [node, str(runner)],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
    )
    if proc.returncode != 0:
        pytest.fail(
            f"newConversationId threw or node failed (exit {proc.returncode}):\n"
            f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}"
        )
    return json.loads(proc.stdout)


def test_new_conversation_id_insecure_context_does_not_throw(helper_results):
    """Reproduce the explorer finding: randomUUID missing must still yield a UUID.

    Before the fix this is exactly ``TypeError: crypto.randomUUID is not a function``.
    """
    results = helper_results
    assert results["insecureOk"] is True
    assert results["insecure"] == results["insecureExpected"]
    assert UUID_V4_RE.match(results["insecure"])


def test_new_conversation_id_prefers_random_uuid_when_present(helper_results):
    assert helper_results["secure"] == "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


def test_new_conversation_id_without_web_crypto(helper_results):
    results = helper_results
    assert results["noCryptoOk"] is True
    assert UUID_V4_RE.match(results["noCrypto"])
    assert results["distinct"] is True
