"""Regression: forking a conversation must work on non-secure HTTP.

``crypto.randomUUID()`` is secure-context-only. On plain ``http://`` hosts
(``window.isSecureContext === false``) it is undefined, and the fork button
threw ``TypeError: crypto.randomUUID is not a function`` before any
conversation state updated — the same chat stayed open and history did not
change.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_JSX = ROOT / "frontend" / "src" / "App.jsx"
ID_JS = ROOT / "frontend" / "src" / "utils" / "id.js"

UUID_V4 = r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"


def test_fork_handler_does_not_call_secure_only_random_uuid():
    """Fork used to call crypto.randomUUID() directly; that throws on http://."""
    text = APP_JSX.read_text(encoding="utf-8")
    assert "crypto.randomUUID" not in text
    assert "newConversationId" in text
    assert "utils/id" in text


def test_new_conversation_id_works_when_random_uuid_is_missing(tmp_path):
    """Reproduce the reported insecure-context crash against the helper.

    The page the explorer evaluated had ``typeof crypto.randomUUID ===
    'undefined'`` while ``crypto.getRandomValues`` still existed. Calling
    ``crypto.randomUUID()`` is the TypeError; the helper must still return
    a v4 UUID in that situation.
    """
    node = shutil.which("node")
    assert node, "node is required to execute the frontend id helper"
    assert ID_JS.is_file(), f"missing portable id helper: {ID_JS}"

    runner = tmp_path / "run_id.mjs"
    helper_url = ID_JS.resolve().as_uri()
    runner.write_text(
        f"""
import {{ newConversationId }} from '{helper_url}';

const UUID_V4 = {UUID_V4!r};

function assertUuid(id, label) {{
  if (typeof id !== 'string' || !new RegExp(UUID_V4, 'i').test(id)) {{
    throw new Error(label + ': not a v4 UUID: ' + String(id));
  }}
}}

// Exact reported crash: randomUUID is not a function.
const insecure = {{
  getRandomValues(arr) {{
    return crypto.getRandomValues(arr);
  }},
}};
let threw = false;
try {{
  insecure.randomUUID();
}} catch (e) {{
  threw = e instanceof TypeError && /randomUUID is not a function/.test(String(e));
}}
if (!threw) {{
  throw new Error('expected TypeError: randomUUID is not a function');
}}

const fromInsecure = newConversationId(insecure);
assertUuid(fromInsecure, 'insecure getRandomValues');

const fromNone = newConversationId(undefined);
assertUuid(fromNone, 'no crypto');

const fromSecure = newConversationId(crypto);
assertUuid(fromSecure, 'secure randomUUID');

const a = newConversationId(insecure);
const b = newConversationId(insecure);
if (a === b) throw new Error('ids must be unique');

console.log(JSON.stringify({{ fromInsecure, fromNone, fromSecure }}));
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [node, str(runner)],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0, (result.stdout or "") + "\n" + (result.stderr or "")
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    for key in ("fromInsecure", "fromNone", "fromSecure"):
        assert re.match(UUID_V4, payload[key], re.I), payload[key]
