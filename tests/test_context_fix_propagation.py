"""
Tests that self-corrections (content_to_remove [TO] normalisation,
edit truncation, and indent fixes) are propagated back into the
assistant message stored in conversation history.

The bug: formatted_tool_calls (a shallow copy) was appended to messages
BEFORE execute_tool_calls ran. apply_self_correction mutated
current_tool_calls dicts but NOT the copies inside messages.

Fix: main_flow.py now does ``assistant_message["tool_calls"] = current_tool_calls``
so the shared dicts see every correction automatically.

Run with:
    cd /workspace/ThinkWithTool && python tests/test_context_fix_propagation.py
"""

import json
import os
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.code_tools.file_operations import apply_self_correction, maybe_truncate_edits
from src.tool_executor import execute_tool_calls
from src.code_sandbox import WORKSPACE

# ═══════════════════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════════════════

_passed = 0
_failed = 0


def build_tc(idx: int, name: str, args: dict) -> dict:
    return {
        "id": f"call_{idx}",
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(args, ensure_ascii=False),
        },
    }


def find_assistant_args(messages: list, call_id: str) -> dict | None:
    """Return the parsed arguments dict for *call_id* in the assistant message."""
    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                if tc.get("id") == call_id:
                    return json.loads(tc["function"]["arguments"])
    return None


def make_file(name: str, content: str) -> str:
    path = os.path.join(WORKSPACE, name)
    Path(path).write_text(content)
    return name


def simulate_main_flow(current_tool_calls: list) -> tuple[list, list]:
    """
    Simulate what main_flow.py does AFTER the fix:
      1. assistant_message["tool_calls"] = current_tool_calls
      2. messages.append(assistant_message)
      3. execute_tool_calls(current_tool_calls, messages)
    """
    assistant_message = {
        "role": "assistant",
        "content": "I'll edit the file.",
        "tool_calls": current_tool_calls,  # shared reference — the fix
    }
    messages = [assistant_message]
    execute_tool_calls(current_tool_calls, messages)
    return messages, current_tool_calls


def assert_equals(a, b, label=""):
    global _passed, _failed
    if a == b:
        _passed += 1
        print(f"  ✅ PASS: {label}")
    else:
        _failed += 1
        print(f"  ❌ FAIL: {label}")
        print(f"     expected: {b!r}")
        print(f"     got:      {a!r}")


def assert_contains(haystack, needle, label=""):
    global _passed, _failed
    if needle in haystack:
        _passed += 1
        print(f"  ✅ PASS: {label}")
    else:
        _failed += 1
        print(f"  ❌ FAIL: {label}")
        print(f"     '{needle}' not found in result")


def assert_not_in(haystack, needle, label=""):
    global _passed, _failed
    if needle not in haystack:
        _passed += 1
        print(f"  ✅ PASS: {label}")
    else:
        _failed += 1
        print(f"  ❌ FAIL: {label}")
        print(f"     '{needle}' unexpectedly found")


# ═══════════════════════════════════════════════════════════════════════
# Test 1: [TO] marker added to content_to_remove
# ═══════════════════════════════════════════════════════════════════════

def test_to_marker_propagated_basic():
    """content_to_remove without [TO] → assistant message gets [TO]."""
    make_file("_t1.py", textwrap.dedent("""\
        def foo():
            print("hello")
            return 42


        def bar():
            print("world")
            return 99
    """))

    tc = build_tc(1, "edit_file", {
        "target_file": "_t1.py",
        "edits": [{
            "remove_line_number": "6-8",
            "content_to_remove": (
                "def bar():\n"
                "    print(\"world\")\n"
                "    return 99"
            ),
            "replace_content": "def bar():\n    return 100",
        }],
    })

    messages, _ = simulate_main_flow([tc])
    args = find_assistant_args(messages, "call_1")
    ctr = args["edits"][0]["content_to_remove"]

    assert_contains(ctr, "\n[TO]\n",
                    "content_to_remove gains [TO] marker")
    # should be just first & last lines with [TO]
    assert_equals(ctr, "def bar():\n[TO]\n    return 99",
                  "content_to_remove has correct [TO] normalised form")

    os.remove(os.path.join(WORKSPACE, "_t1.py"))


# ═══════════════════════════════════════════════════════════════════════
# Test 2: [TO] marker with single-line range (no [TO] originally)
# ═══════════════════════════════════════════════════════════════════════

def test_to_marker_not_added_for_single_line():
    """Single-line edit → no [TO] added (it's not multi-line)."""
    make_file("_t2.py", "old_line\nother\n")

    tc = build_tc(1, "edit_file", {
        "target_file": "_t2.py",
        "edits": [{
            "remove_line_number": "1",
            "content_to_remove": "old_line",
            "replace_content": "new_line",
        }],
    })

    messages, _ = simulate_main_flow([tc])
    args = find_assistant_args(messages, "call_1")
    ctr = args["edits"][0]["content_to_remove"]

    # Single-line: no [TO] should be added
    assert_not_in(ctr, "\n[TO]\n",
                  "single-line content_to_remove does NOT get [TO]")
    assert_equals(ctr, "old_line",
                  "single-line content_to_remove stays unchanged")

    os.remove(os.path.join(WORKSPACE, "_t2.py"))


# ═══════════════════════════════════════════════════════════════════════
# Test 3: already-correct [TO] stays correct
# ═══════════════════════════════════════════════════════════════════════

def test_to_marker_preserved_when_already_correct():
    """When user already uses [TO] format, it stays as-is."""
    make_file("_t3.py", textwrap.dedent("""\
        a
        b
        c
        d
        e
    """))

    tc = build_tc(1, "edit_file", {
        "target_file": "_t3.py",
        "edits": [{
            "remove_line_number": "2-4",
            "content_to_remove": "b\n[TO]\nd",
            "replace_content": "X",
        }],
    })

    messages, _ = simulate_main_flow([tc])
    args = find_assistant_args(messages, "call_1")
    ctr = args["edits"][0]["content_to_remove"]

    assert_contains(ctr, "\n[TO]\n",
                    "[TO] marker preserved when already present")
    assert_equals(ctr, "b\n[TO]\nd",
                  "already-correct [TO] content_to_remove unchanged")

    os.remove(os.path.join(WORKSPACE, "_t3.py"))


# ═══════════════════════════════════════════════════════════════════════
# Test 4: maybe_truncate_edits — >3 edits truncated
# ═══════════════════════════════════════════════════════════════════════

def test_truncate_to_three_edits():
    """5 edits → truncated to 3 in assistant message."""
    make_file("_t4.py", "a\nb\nc\nd\ne\nf\n")

    tc = build_tc(1, "edit_file", {
        "target_file": "_t4.py",
        "edits": [
            {"remove_line_number": "1", "content_to_remove": "a", "replace_content": "A1"},
            {"remove_line_number": "2", "content_to_remove": "b", "replace_content": "B2"},
            {"remove_line_number": "3", "content_to_remove": "c", "replace_content": "C3"},
            {"remove_line_number": "4", "content_to_remove": "d", "replace_content": "D4"},
            {"remove_line_number": "5", "content_to_remove": "e", "replace_content": "E5"},
        ],
    })

    messages, _ = simulate_main_flow([tc])
    args = find_assistant_args(messages, "call_1")

    assert_equals(len(args["edits"]), 3,
                  "edits truncated to 3")
    assert_equals(args["edits"][2]["remove_line_number"], "3",
                  "third edit is the original third (1-indexed)")

    os.remove(os.path.join(WORKSPACE, "_t4.py"))


# ═══════════════════════════════════════════════════════════════════════
# Test 5: exactly 3 edits — no truncation needed
# ═══════════════════════════════════════════════════════════════════════

def test_no_truncate_when_exactly_three():
    """Exactly 3 edits → no truncation, all 3 preserved."""
    make_file("_t5.py", "a\nb\nc\n")

    tc = build_tc(1, "edit_file", {
        "target_file": "_t5.py",
        "edits": [
            {"remove_line_number": "1", "content_to_remove": "a", "replace_content": "A"},
            {"remove_line_number": "2", "content_to_remove": "b", "replace_content": "B"},
            {"remove_line_number": "3", "content_to_remove": "c", "replace_content": "C"},
        ],
    })

    messages, _ = simulate_main_flow([tc])
    args = find_assistant_args(messages, "call_1")

    assert_equals(len(args["edits"]), 3,
                  "exactly 3 edits — not truncated")

    os.remove(os.path.join(WORKSPACE, "_t5.py"))


# ═══════════════════════════════════════════════════════════════════════
# Test 6: truncation + [TO] correction together
# ═══════════════════════════════════════════════════════════════════════

def test_truncate_plus_to_correction():
    """>3 edits, one with no [TO] → both truncation and [TO] propagate."""
    make_file("_t6.py", textwrap.dedent("""\
        def a(): pass
        def b(): pass
        def c():
            return 1
        def d(): pass
        def e(): pass
    """))

    tc = build_tc(1, "edit_file", {
        "target_file": "_t6.py",
        "edits": [
            {"remove_line_number": "1", "content_to_remove": "def a(): pass", "replace_content": "# a"},
            # edit #2: multi-line without [TO] — should get corrected
            {"remove_line_number": "3-4",
             "content_to_remove": "def c():\n    return 1",
             "replace_content": "def c():\n    return 99"},
            {"remove_line_number": "2", "content_to_remove": "def b(): pass", "replace_content": "# b"},
            # edit #4 & #5 — should be truncated off
            {"remove_line_number": "5", "content_to_remove": "def d(): pass", "replace_content": "# d"},
            {"remove_line_number": "6", "content_to_remove": "def e(): pass", "replace_content": "# e"},
        ],
    })

    messages, _ = simulate_main_flow([tc])
    args = find_assistant_args(messages, "call_1")

    assert_equals(len(args["edits"]), 3,
                  "truncated to 3 edits (combined corrections)")
    # edit #2 (now index 1 after truncation keeping first 3) should have [TO]
    ctr = args["edits"][1]["content_to_remove"]
    assert_contains(ctr, "\n[TO]\n",
                    "second edit's content_to_remove got [TO] marker")

    os.remove(os.path.join(WORKSPACE, "_t6.py"))


# ═══════════════════════════════════════════════════════════════════════
# Test 7: line numbers corrected in remove_line_number
# ═══════════════════════════════════════════════════════════════════════

def test_remove_line_number_corrected():
    """When anchor tolerance shifts the match, remove_line_number is updated."""
    make_file("_t7.py", "X\nA\nB\nC\nY\n")

    tc = build_tc(1, "edit_file", {
        "target_file": "_t7.py",
        "edits": [{
            "remove_line_number": "3",   # says line 3, but "A" is at line 2
            "content_to_remove": "A",
            "replace_content": "FOUND",
        }],
    })

    messages, _ = simulate_main_flow([tc])
    args = find_assistant_args(messages, "call_1")

    rln = args["edits"][0]["remove_line_number"]
    assert_equals(rln, "2-2",
                  "remove_line_number corrected from '3' to '2-2'")

    os.remove(os.path.join(WORKSPACE, "_t7.py"))


# ═══════════════════════════════════════════════════════════════════════
# Test 8: indent auto-fix propagation
# ═══════════════════════════════════════════════════════════════════════

def test_indent_fix_propagated():
    """When content_to_remove has wrong indent, replace_content is adjusted."""
    make_file("_t8.py", "    def foo():\n        pass\n")

    tc = build_tc(1, "edit_file", {
        "target_file": "_t8.py",
        "edits": [{
            "remove_line_number": "1",
            "content_to_remove": "  def foo():",   # 2-space (wrong)
            "replace_content": "  def bar():",     # 2-space → should become 4-space
        }],
    })

    messages, _ = simulate_main_flow([tc])
    args = find_assistant_args(messages, "call_1")

    # The marker should keep the ORIGINAL replace_content (2-space),
    # since the point is that the tool tolerates what the LLM sent.
    # Actually, let's check what the file looks like...
    content = Path(os.path.join(WORKSPACE, "_t8.py")).read_text()
    assert_equals(content, "    def bar():\n        pass\n",
                  "file content has correct 4-space indent (execution worked)")

    # The correction marker keeps the original replace_content as sent by LLM
    repl = args["edits"][0]["replace_content"]
    assert_equals(repl, "  def bar():",
                  "replace_content in marker stays as LLM sent it")

    os.remove(os.path.join(WORKSPACE, "_t8.py"))


# ═══════════════════════════════════════════════════════════════════════
# Test 9: multiple tool calls in one batch all get corrected
# ═══════════════════════════════════════════════════════════════════════

def test_multiple_tool_calls_all_corrected():
    """Two separate edit_file calls in one turn — both get corrections propagated."""
    make_file("_t9a.py", "A\nB\nC\nD\n")
    make_file("_t9b.py", "W\nX\nY\nZ\n")

    tc1 = build_tc(1, "edit_file", {
        "target_file": "_t9a.py",
        "edits": [{
            "remove_line_number": "2-3",
            "content_to_remove": "B\nC",   # no [TO]
            "replace_content": "BB\nCC",
        }],
    })
    tc2 = build_tc(2, "edit_file", {
        "target_file": "_t9b.py",
        "edits": [{
            "remove_line_number": "2-3",
            "content_to_remove": "X\nY",   # no [TO]
            "replace_content": "XX\nYY",
        }],
    })

    messages, current = simulate_main_flow([tc1, tc2])

    args1 = find_assistant_args(messages, "call_1")
    args2 = find_assistant_args(messages, "call_2")

    assert_contains(args1["edits"][0]["content_to_remove"], "\n[TO]\n",
                    "call_1 gets [TO]")
    assert_contains(args2["edits"][0]["content_to_remove"], "\n[TO]\n",
                    "call_2 gets [TO]")

    os.remove(os.path.join(WORKSPACE, "_t9a.py"))
    os.remove(os.path.join(WORKSPACE, "_t9b.py"))


# ═══════════════════════════════════════════════════════════════════════
# Test 10: non-edit_file calls are unaffected
# ═══════════════════════════════════════════════════════════════════════

def test_read_file_args_unchanged():
    """read_file tool call in same batch — arguments untouched."""
    make_file("_t10.py", "hello\n")

    tc1 = build_tc(1, "edit_file", {
        "target_file": "_t10.py",
        "edits": [{
            "remove_line_number": "1",
            "content_to_remove": "hello",
            "replace_content": "world",
        }],
    })
    tc2 = build_tc(2, "read_file", {
        "target_file": "_t10.py",
    })

    messages, _ = simulate_main_flow([tc1, tc2])

    args2 = find_assistant_args(messages, "call_2")
    assert_equals(args2["target_file"], "_t10.py",
                  "read_file args unchanged")

    os.remove(os.path.join(WORKSPACE, "_t10.py"))


# ═══════════════════════════════════════════════════════════════════════
# Test 11: deletion with content_to_remove="" stays as-is
# ═══════════════════════════════════════════════════════════════════════

def test_empty_content_to_remove_unchanged():
    """Deleting an empty line: content_to_remove='' → stays ''."""
    make_file("_t11.py", "a\n\nb\n")

    tc = build_tc(1, "edit_file", {
        "target_file": "_t11.py",
        "edits": [{
            "remove_line_number": "2",
            "content_to_remove": "",
            "replace_content": "X",
        }],
    })

    messages, _ = simulate_main_flow([tc])
    args = find_assistant_args(messages, "call_1")

    assert_equals(args["edits"][0]["content_to_remove"], "",
                  "empty content_to_remove stays empty")

    os.remove(os.path.join(WORKSPACE, "_t11.py"))


# ═══════════════════════════════════════════════════════════════════════
# Test 12: context_manager discovers files from corrected messages
# ═══════════════════════════════════════════════════════════════════════

def test_context_manager_sees_corrected_args():
    """discover_open_files() parses the corrected assistant message args."""
    from src.code_tools.context_manager import discover_open_files

    make_file("_t12.py", "X\nY\nZ\n")

    tc = build_tc(1, "edit_file", {
        "target_file": "_t12.py",
        "edits": [{
            "remove_line_number": "2-3",
            "content_to_remove": "Y\nZ",   # no [TO]
            "replace_content": "YY\nZZ",
        }],
    })

    messages, _ = simulate_main_flow([tc])

    # discover_open_files must still find the file
    open_files = discover_open_files(messages)
    assert_contains(open_files, "_t12.py",
                    "context_manager discovers file from corrected message")

    # The args in the message are parseable JSON with [TO]
    args = find_assistant_args(messages, "call_1")
    assert args is not None, "corrected args are valid JSON"

    os.remove(os.path.join(WORKSPACE, "_t12.py"))


# ═══════════════════════════════════════════════════════════════════════
# Test 13: original tool result has no SELF_CORRECT marker (stripped)
# ═══════════════════════════════════════════════════════════════════════

def test_tool_result_no_self_correct_marker():
    """The SELF_CORRECT marker is stripped from the tool result message."""
    make_file("_t13.py", "A\nB\nC\nD\n")

    tc = build_tc(1, "edit_file", {
        "target_file": "_t13.py",
        "edits": [{
            "remove_line_number": "2-3",
            "content_to_remove": "B\nC",
            "replace_content": "BB\nCC",
        }],
    })

    messages, _ = simulate_main_flow([tc])

    # The tool result (messages[1]) should NOT contain SELF_CORRECT
    tool_msg = messages[1]["content"]
    assert_not_in(tool_msg, "SELF_CORRECT",
                  "tool result message has no SELF_CORRECT marker")

    os.remove(os.path.join(WORKSPACE, "_t13.py"))


# ═══════════════════════════════════════════════════════════════════════
# Test 14: delete_file → file removed from context
# ═══════════════════════════════════════════════════════════════════════

def test_delete_file_removed_from_context():
    """After delete_file, context_manager does not list the file."""
    from src.code_tools.context_manager import discover_open_files

    make_file("_t14.py", "data\n")

    # First: read the file so it's "open"
    tc_read = build_tc(1, "read_file", {"target_file": "_t14.py"})
    messages, _ = simulate_main_flow([tc_read])
    assert_contains(discover_open_files(messages), "_t14.py",
                    "file is in context after read_file")

    # Then: delete it in a new turn
    tc_del = build_tc(2, "delete_file", {"target_file": "_t14.py"})
    messages, _ = simulate_main_flow([tc_del])
    assert_not_in(discover_open_files(messages), "_t14.py",
                  "file is removed from context after delete_file")

    # Clean up (file already deleted)
    try:
        os.remove(os.path.join(WORKSPACE, "_t14.py"))
    except OSError:
        pass


# ═══════════════════════════════════════════════════════════════════════
# Test 15: write_file args survive unmodified
# ═══════════════════════════════════════════════════════════════════════

def test_write_file_args_unchanged():
    """write_file call — args are passed through unchanged."""
    tc = build_tc(1, "write_file", {
        "target_file": "_t15.py",
        "code_edit": "print('hello')",
    })

    messages, _ = simulate_main_flow([tc])
    args = find_assistant_args(messages, "call_1")

    assert_equals(args["target_file"], "_t15.py",
                  "write_file target_file unchanged")
    assert_equals(args["code_edit"], "print('hello')",
                  "write_file code_edit unchanged")

    os.remove(os.path.join(WORKSPACE, "_t15.py"))


# ═══════════════════════════════════════════════════════════════════════
# Test 16: same-turn same-file guard does not mutate assistant args
# ═══════════════════════════════════════════════════════════════════════

def test_same_file_guard_preserves_args():
    """Second edit to same file in one turn → guard error, args untouched."""
    make_file("_t16.py", "a\nb\nc\n")

    tc1 = build_tc(1, "edit_file", {
        "target_file": "_t16.py",
        "edits": [{
            "remove_line_number": "1",
            "content_to_remove": "a",
            "replace_content": "A",
        }],
    })
    tc2 = build_tc(2, "edit_file", {
        "target_file": "_t16.py",        # same file!
        "edits": [{
            "remove_line_number": "2",
            "content_to_remove": "b",
            "replace_content": "B",
        }],
    })

    messages, _ = simulate_main_flow([tc1, tc2])

    # call_1 should have been edited
    args1 = find_assistant_args(messages, "call_1")
    assert args1 is not None, "call_1 args are present"

    # call_2 should have its original args (guard error is returned as tool result)
    args2 = find_assistant_args(messages, "call_2")
    assert_equals(args2["edits"][0]["replace_content"], "B",
                  "call_2 args preserved (guard error, not mutation)")

    os.remove(os.path.join(WORKSPACE, "_t16.py"))


# ═══════════════════════════════════════════════════════════════════════
# runner
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 65)
    print("Context fix propagation tests")
    print("=" * 65)

    tests = [
        ("[TO] marker propagated (basic)", test_to_marker_propagated_basic),
        ("[TO] not added for single-line", test_to_marker_not_added_for_single_line),
        ("[TO] preserved when already correct", test_to_marker_preserved_when_already_correct),
        ("truncation >3 edits", test_truncate_to_three_edits),
        ("no truncation at exactly 3 edits", test_no_truncate_when_exactly_three),
        ("truncation + [TO] combined", test_truncate_plus_to_correction),
        ("remove_line_number corrected", test_remove_line_number_corrected),
        ("indent fix propagated", test_indent_fix_propagated),
        ("multiple tool calls all corrected", test_multiple_tool_calls_all_corrected),
        ("non-edit-file args unchanged", test_read_file_args_unchanged),
        ("empty content_to_remove unchanged", test_empty_content_to_remove_unchanged),
        ("context_manager sees corrected args", test_context_manager_sees_corrected_args),
        ("tool result has no SELF_CORRECT marker", test_tool_result_no_self_correct_marker),
        ("delete_file removes from context", test_delete_file_removed_from_context),
        ("write_file args unchanged", test_write_file_args_unchanged),
        ("same-file guard preserves args", test_same_file_guard_preserves_args),
    ]

    for i, (name, fn) in enumerate(tests, 1):
        try:
            fn()
        except Exception as e:
            _failed += 1
            import traceback
            print(f"  ❌ FAIL: {name} — UNHANDLED EXCEPTION: {e}")
            traceback.print_exc()

    print()
    print("=" * 65)
    total = _passed + _failed
    print(f"Results: {_passed}/{total} passed", end="")
    if _failed:
        print(f", {_failed} FAILED")
        sys.exit(1)
    else:
        print(" — ALL PASSED 🎉")
