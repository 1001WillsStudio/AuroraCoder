# Edit File Failure Analysis

## Session: 2026-05-13 — Agent Docker Log Improvements

This document analyzes every `edit_file` failure encountered during the logging/latency improvement session, categorises root causes, and derives corrective practices.

---

## 1. Failure Inventory

| # | File | Attempt | Error Type | Root Cause Category |
|---|------|---------|------------|---------------------|
| 1 | `supervisord.conf` | 1st | `end_content` mismatch (fluxbox: expected `priority=20` at line 14, actual was `autorestart=true`) | **Unverified boundary assumption** |
| 2 | `providers.py` | 1st | Double `try:` inserted (edits #2 and #3 overlapped in `_init_credentials`) | **Overlapping intra-batch edits** |
| 3 | `run_web.py` | 1st | `start_content` mismatch at line 49 (expected `log_level="info"`, line had already shifted to `reload=False`) | **Intra-batch line drift** |
| 4 | `main_flow.py` | 1st | Timing log placed at wrong indentation (8 spaces = same level as `try:`/`except:`, causing `SyntaxError`) | **Indentation blindness** |
| 5 | `main_flow.py` | 2nd | `end_content` mismatch (`        )` vs differently-indented `)`) | **Stale content assumption** |
| 6 | `main_flow.py` | 3rd | `start_content` mismatch (lines shifted by prior successful edits in same batch) | **Intra-batch line drift** |
| 7 | `web_api/app.py` | 1st | `start_content` mismatch at line 434 (expected `return StreamingResponse(` but line was blank) | **Stale line numbers** |

**Total**: 7 failures across 4 files, taking 8 `edit_file` calls to accomplish what should have taken 5.

---

## 2. Root Cause Categories

### Category A: Unverified Boundary Assumption

**Incident**: `supervisord.conf` edit #1, fluxbox section.

**What happened**: I assumed all four VNC-infrastructure program sections had identical structure (4 lines: header, command, autorestart, priority). The fluxbox section actually had 5 lines because it includes `environment=DISPLAY=":99"`.

**Why**: Pattern-matching shortcut — I saw three other sections were 4 lines and didn't verify the fourth. The `end_line_content` `priority=20` was correct but the line number (14) pointed to `autorestart=true`.

**Corrective rule**:
- **Verify every boundary individually**. Never assume structural uniformity across sections.
- When editing multiple similar blocks, check the `end_line` + `end_line_content` of EVERY block before submitting the batch.

---

### Category B: Overlapping Intra-Batch Edits

**Incident**: `providers.py` edit #1, edits #2 and #3 clashing on `_init_credentials`.

**What happened**:
- Edit #2 replaced line 55 (original `from google.auth import default`) with a new block starting with `try: from google.auth import default ...`
- Edit #3 was supposed to replace lines 56-65 (the original try/except body) with a new except block
- Both edits inserted `try:` at their respective boundaries → final file had TWO consecutive `try:` statements

**Why**: The edits were numbered against the ORIGINAL file, but edit #2's replacement content added a `try:` that edit #3 also started with. The edit tool applies edits sequentially within a batch using original line numbers, so earlier edits' replacement content isn't accounted for in later edits' boundary checks.

**Corrective rules**:
- **Avoid overlapping edit ranges within a single batch call**. If two edits touch adjacent lines or overlapping regions, split them into separate `edit_file` calls.
- **Prefer single larger replacement** over multiple small overlapping ones. Replace the entire method/block instead of piecemeal.

---

### Category C: Intra-Batch Line Drift

**Incidents**: `run_web.py` 1st attempt (edit #2 failed after edit #1 shifted lines), `main_flow.py` 3rd attempt (indentation fix failed after line shifts from prior edits in same batch).

**What happened**: When a batch contains multiple edits to the same file, earlier edits that change the number of lines cause ALL subsequent edit boundaries in the same batch to be wrong. The tool reports `start_content does not match` or `end_content does not match` because line numbers were computed against the original file layout.

**Why**: The edit tool evaluates all edit boundaries against the ORIGINAL file content before applying any. When edit #1 adds/removes lines, edit #2's `start_line` points to wrong content. This is fundamental to the tool's design.

**Corrective rules**:
- **ONE `edit_file` call = ONE conceptual change**. Batch edits ONLY when they are independent (non-sequential, non-overlapping, and don't change line counts in ways that affect each other).
- **When in doubt, split into separate calls**. The overhead of an extra `edit_file` call is negligible compared to debugging a failed batch.

---

### Category D: Indentation Blindness

**Incident**: `main_flow.py` 1st attempt — timing log placed at same indentation as `try:`/`except:`.

**What happened**: The replacement content for the timing log was written at 8-space indentation (same level as `try:` and `except Exception as e:`). This placed code BETWEEN the try body and the except clause, which is illegal Python (`SyntaxError`).

**Why**: I focused on WHAT the code should do (log timing) and the LINE NUMBER where it should go, but neglected to verify its indentation level relative to the surrounding control-flow structure. The timing code needed to be at 12 spaces (inside `try:`) to be valid.

**Visualisation of the error**:
```python
        try:                          # 8 spaces
            for chunk in stream:      # 12 spaces
                ...                   # 16 spaces
                                      #
        # Log timing ...              # 8 spaces  <-- SAME AS try/except! SYNTAX ERROR
        _main_logger.info(...)        # 8 spaces
                                      #
        except Exception as e:        # 8 spaces
```

**Corrective rules**:
- **Always visualise the control-flow context** of the target location before writing replacement content. Ask: "What block am I inside — try? for? if? What indentation does that require?"
- **Count from the enclosing block**: If inserting inside a `try:` at 8 spaces, content must be at >=12 spaces. If inside a `for` at 12 spaces, content must be at >=16 spaces.
- **Check the surrounding lines' indentation** in the replacement region to calibrate.

---

### Category E: Stale Content Assumption

**Incidents**: `main_flow.py` 2nd attempt, `web_api/app.py` 1st attempt.

**What happened**: After previous edits (in earlier `edit_file` calls) changed the file, I specified `end_line_content` or `start_line_content` based on my memory of what the content SHOULD be, not what it actually WAS after the shifts.

**Why**: Mental model was stale. After a batch of edits succeeds, the file's line numbers and sometimes content shift. I didn't re-read the affected region before making follow-up edits.

**Corrective rules**:
- **After ANY successful edit to a file, re-read the affected region** before making follow-up edits to the same file.
- **Use `read_file`** (not memory) to get current line numbers and boundary content for subsequent `edit_file` calls.
- If the edit tool reports a line-number shift warning ("Line numbers after the edited region(s) have shifted by +N"), treat it as a red flag that ALL subsequent line numbers for that file must be recalculated.

---

## 3. Impact Summary

| Category | Occurrences | Severity |
|----------|-------------|----------|
| A — Unverified boundary | 1 | Medium (caught immediately, easy fix) |
| B — Overlapping edits | 1 | High (produced broken code: double `try:`) |
| C — Intra-batch line drift | 3 | Medium (fails fast, but wastes iterations) |
| D — Indentation blindness | 1 | High (would cause runtime `SyntaxError`) |
| E — Stale content | 2 | Low (fails fast, easy to fix by re-reading) |

---

## 4. Process Improvements

### Before every `edit_file` call:

1. **Single-purpose batches**: One conceptual change per `edit_file` call. Don't batch edits that touch the same file in ways that affect each other's line numbers.

2. **Verify every boundary**: For each edit in the batch, confirm `start_line_content` and `end_line_content` match the CURRENT file (use `read_file` if needed).

3. **Check for overlap**: No two edits in a batch should touch the same or adjacent lines. If they do, split into separate calls.

4. **Calibrate indentation**: When replacing code inside a control-flow structure (`try`, `for`, `if`, `while`, `def`), explicitly check the indentation level of surrounding lines and match it.

5. **Post-edit re-read**: After a successful edit, immediately re-read the changed region to update the mental model before the next edit to the same file.

6. **Heed the shift warning**: When the tool reports "Line numbers after the edited region(s) have shifted by +N", recalculate ALL line numbers for subsequent edits to that file.

---

## 5. What Worked Well

The following patterns succeeded on the first try:

- **Single-target, isolated edits**: Changing `uvicorn` log levels in `conversation_gateway/api.py` (one-line change, no side effects) succeeded instantly.
- **Small, non-overlapping batches**: The `web_api/app.py` edit (single-line change from `logger.info` to `logger.debug`) succeeded because it was isolated and didn't shift any lines that other edits depended on.
- **Post-fix verification**: After discovering the double-`try:` bug in `providers.py`, the fix was a single, precise edit targeting exactly the two duplicated lines — no ambiguity.

---

## 6. Corrections (added 2026-05-13)

The "Category C: Intra-Batch Line Drift" diagnosis above is **incorrect**. The edit tool validates ALL anchors against the ORIGINAL file before applying any changes, then applies edits bottom-to-top. This means intra-batch line drift cannot occur at the tool level.

The real root cause for incidents #3, #5, #6, and #7 is **stale mental model**: the agent provided anchor content from memory (based on the file state before a prior successful edit) instead of reading the current line numbers from the code interpreter display. The tool correctly rejected these — no data was corrupted.

### Hardening applied

The following changes were made to `file_operations.py` and `tool_definitions.py`:

1. **Error messages now point to the code interpreter**: every anchor mismatch error appends "Check the code interpreter display below for the current file content and correct line numbers."
2. **Indentation-only mismatches get a specific hint**: "Content matches but indentation differs — expected N leading spaces, got M."
3. **Python syntax validation**: for `.py` files, `compile()` is run on the result before writing. If the edit would break syntax (e.g. wrong indentation), the file is NOT modified and the SyntaxError is returned.
4. **Tool description updated**: explicitly states the code interpreter is the ONLY reliable source for line numbers. Clarifies that batched edits use original line numbers and are applied bottom-to-top.
