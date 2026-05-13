# edit_file Error Patterns (observed in practice)

## Pattern 1: Batch-edit atomic failure

**What**: Submitting multiple `edits` in a single `edit_file` call. If *any* edit fails
validation, *none* are applied — but the file display already shifted from prior edits.

**Consequence**: You see the stale display, fix the failing edit, re-submit — but now
the other (previously successful) edits are lost and the ranges they targeted may have
already changed.

**Rule**: One edit per `edit_file` call when modifying the same function. Only batch
edits that target completely disjoint, non-overlapping regions far apart in the file.

---

## Pattern 2: Deleting content that straddles a logical boundary

**What**: An edit range that eats a line belonging to an outer construct (e.g., the
closing `)` of a function call whose `(` is outside the edited range).

**Example**: `wait_for(` on line 144, its `)` on a line inside the edit range — the
replacement dropped the `)`, producing `SyntaxError: '(' was never closed`.

**Rule**: Before confirming an edit, check whether the first and last lines of the
range belong to an outer syntactic construct (parens, brackets, try/except, if/else).
If so, narrow the range or include the construct boundary.

---

## Pattern 3: Variable flip-flop from overlapping successive edits

**What**: Edit A introduces variable X and removes Y. Edit B introduces Y and uses X.
But edit B's replacement was written assuming edit A's output — if edit A failed or
was re-done, X and Y desync.

**Example**: `body_size` and `cid_tag` were added/removed/readded across 4 separate
edits because each edit didn't account for the other's state.

**Rule**: Compute all needed variables in ONE edit. If you forgot one, re-read the
file, then replace the entire block with all variables computed atomically.

---

## Pattern 4: Editing without re-reading after line shifts

**What**: An edit succeeds (+N lines). The next edit uses line numbers from before
the shift. `start_line_content` matches but the range now covers unintended lines.

**Rule**: After ANY successful edit, re-read the affected region before the next edit.
NEVER chain edits to the same file without re-reading.

---

## Pattern 5: Replacing a range that includes the `except` clause of a `try`

**What**: An edit range ends inside a `try` body, and the replacement lacks the
`except`/`finally` that the original had below the range. The `try:` from above
the range now has no matching clause.

**Example**: Replaced `timeout=2.0,\n)` with `except ...: pass`, losing both the
closing `)` AND the `except` from the original. Required a follow-up edit to add
the `)` back, then another to fix the `try/except`.

**Rule**: When an edit range includes lines inside a `try` block, check whether
the `except`/`finally` below the range will still be valid. If not, include them
in the replacement.
