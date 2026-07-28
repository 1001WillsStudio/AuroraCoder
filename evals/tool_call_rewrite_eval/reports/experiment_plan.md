# Experiment Plan: Evaluating Tool-Call Rewrite (vs. Leaving the Wrong Call) on Future Edit-Format Adherence

Status: DRAFT for review
Owner: AuroraCoder evaluation
Related docs: `AuroraCoder/src/tool_executor.py`, `AuroraCoder/src/code_tools/file_operations.py`, `AuroraCoder/src/code_tools/edit_file.py`

---

## 1. The innovation under test

AuroraCoder's signature behavior is **silent tool-call rewrite in conversation history**, not just auto-fix of the file.

Mechanism (verified in code):

- `_execute_single_tool` (tool_executor.py:110-118) **always** overwrites the assistant message's stored arguments:
  ```python
  arguments, result = execute_tool_call(tool_name, arguments, ...)
  tool_call["function"]["arguments"] = json.dumps(arguments, ensure_ascii=False)
  ```
- For `edit_file`, `execute_edit_file` (file_operations.py:195-209) returns:
  ```python
  return result, applied if applied is not None else arguments
  ```
  - **SUCCESS after auto-correction**: `RangeReplaceEditor.edit` returns `(result, self.applied_arguments)` where `applied_arguments` is the canonical form `{"file":..., "edits":[{"remove_line_number":"13-15","content_to_remove":"<start>\n[TO]\n<end>","replace_content":...}]}` (edit_file.py:308-311, 560-564). So the model's *own prior tool_call* in history is replaced with the **correct template**.
  - **FAILURE (anchor not found / overlap / no-change)**: `editor.edit` returns `(error_msg, None)` (edit_file.py:254, 270, 300, 376-449). Then `applied is None` → returns the **original raw (malformed) arguments**. The model's *own prior tool_call* in history is the **wrong template**.

So what the model sees in its own history diverges depending on whether the engine could auto-correct. This is the variable we manipulate and measure.

## 2. Hypothesis

**H1 (rewiring helps):** When the prior failed/wrong `edit_file` call is rewritten to the canonical AuroraCoder template in history, the *next* `edit_file` call the model emits is more likely to use the correct anchor format (`remove_line_number` + `[TO]`-delimited `content_to_remove` + `replace_content`) than when the prior wrong call is left untouched in history.

**H0:** The state of the immediately-prior `edit_file` tool_call in history has no effect on the format of the next `edit_file` call (i.e., the rewrite innovation has no measurable downstream value).

H1 being supported would justify the rewrite as a *teaching/demonstration signal* beyond its immediate file-fixing value.

## 3. Independent variable (the treatment)

We hold everything fixed and only change whose tool-call text occupies the one assistant `tool_calls` slot for `edit_file` in the conversation history *before* the probe edit request:

| Arm | History-state of the prior `edit_file` tool_call | AuroraCoder behavior it emulates |
|---|---|---|
| **A — `baseline_no_rewrite`** | The model's original *malformed/wrong* edit args are left verbatim in history (the failure path: `applied is None`). | Current behavior when the engine cannot auto-correct (anchor not found). |
| **B — `rewrite_to_template`** | The prior `edit_file` tool_call args are replaced with the canonical `corrected_edit` template (the success-after-autofix path), but **no extra instruction** is added. | Current behavior when the engine auto-corrects successfully. |

Both arms receive the **same** downstream tool result (the canonical "⚠️ Original parameters were auto-corrected. No action needed from you." success message for B; the anchor-not-found error message for A) — see §6.2 for the matched-result control that isolates the *rewrite* from the *error text*.

## 4. Dependent variable (the metric)

The metric is **format adherence of the next `edit_file` call** (the "probe" edit) issued by the model after the seeded history.

### 4.1 Strict definition of "correct format"

An emitted `edit_file` tool_call is **format-correct** (binary) iff ALL of:

1. `arguments` is valid JSON and `arguments["file"]` is a non-empty string.
2. `arguments["edits"]` is a non-empty list of edit dicts.
3. Each edit dict contains exactly the three canonical keys `remove_line_number`, `content_to_remove`, `replace_content` (no stray keys, mirroring the schema validation at tool_definitions.py:499-509).
4. Each `remove_line_number` ∈ `{"N", "N-M"}` with `1 ≤ sl ≤ el` (matches `_parse_line_range`, edit_file.py:329-349).
5. Each `content_to_remove` either (a) is single-line content, or (b) for a multi-line range contains exactly one `\n[TO]\n` separator whose two halves are non-empty (matches `has_to` parse at edit_file.py:482-491).
6. The anchor `content_to_remove` header line actually matches a real line in the target file at/near the stated `remove_line_number` (anchor finds a hit under `find_anchor_tolerant`; this is the real "would it apply" test, decoupled from whether the *intent* of the edit was what we asked).

### 4.2 Secondary, finer-grained metrics (logged for analysis, not the primary endpoint)

- `format_correct` (primary, binary) — §4.1 all-pass.
- `syntax_correct` — §4.1 (1)-(5) only (format valid even if anchor not located). Decomposes *structure of the call* vs. *grounding in the file*.
- `anchor_resolvable` — §4.1 (6) only (call grounded even if a key field had a stray key, etc.).
- `n_format_violations` — count of which of (1)-(6) failed (Poisson-style covariate; lets us see *which* aspects the wrong-history contaminates: line-number syntax? `[TO]` usage? stray keys?).
- ` Latency / `n_turns_to_success`` — optional: how many extra edits before a correctly-formatted+applied edit occurs (only if we let it iterate).

### 4.3 Ground-truth judge

Format correctness (§4.1) is **deterministic code**, not an LLM judge. We reuse the real engine: for the probe call we run `RangeReplaceEditor._validate_one_edit` and the anchor search; if it returns a `(start_idx,...)` (not an error string) and the parsed args satisfy (1)-(5), the call is format-correct. This makes the metric reproducible and reviewer-inspectable.

## 5. Experimental units, datasets, and minimization of confounds

### 5.1 A "trial" = (file_state, seeded_wrong_prior_edit, probe_edit_task)

- **file_state**: a snapshot of a real file (drawn from a fixed corpus, §5.3). Identical file bytes in both arms.
- **seeded_wrong_prior_edit**: a hand-authored *malformed* `edit_file` tool_call that exercises one specific failure mode (§5.2). Identical across arms.
- **probe_edit_task**: a natural-language instruction asking the model to make one more edit to the same file. Identical across arms, and *independent* of the seeded edit (different line region / different intent) so success isn't trivially contaminated by the prior edit.

### 5.2 Seeded wrong-prior-edit failure modes (orthogonal axes)

We seed each failure mode separately so we can report per-mode lift. All produce a malformed call that the engine *cannot* resolve, i.e. currently yields `applied=None` (Arm A's history) — and whose canonical form (Arm B) we construct via `_validate_one_edit`'s `corrected_edit` against the *ground-truth* intended range:

1. **F1 — wrong line numbers, correct anchor text** (line hint off by >`MAX_ANCHOR_SHIFT=3`): `remove_line_number` wrong, `content_to_remove` correct. Engine currently *can* auto-fix this via `find_anchor_anywhere` (edit_file.py:165-189) → so this is the "auto-fix succeeds" case, useful as an **sanity/control** that B's template ≈ what the engine would produce anyway.
2. **F2 — partially-wrong anchor text** (typo in header line): `content_to_remove` header doesn't match file at the stated line within ±3 and not anywhere. Engine returns `applied=None`. Arm B replaces with the *corrected* header drawn from the file. **High-signal mode.**
3. **F3 — missing `[TO]` on a multi-line range**: `content_to_remove` given as a single multi-line blob with no `\n[TO]\n` separator, but `remove_line_number` denotes a range. Engine `has_to=False` path computes end from start content (edit_file.py:531-532); this often *applies* but produces a non-canonical (template-violating) call — so Arm A's history shows the non-canonical form, Arm B shows the `[TO]` form. **Tests the template-teaching hypothesis directly.**
4. **F4 — stray/duplicate keys + reversed range** (e.g. `end_line_number` left over from a different tool's schema, `remove_line_number="15-13"`): schema drops the stray key (tool_definitions.py:506) and engine errors on `sl>el`. Arm B shows the clean canonical form. **Tests schema-reset behavior.**
5. **F5 — empty `replace_content` + single-line `content_to_remove` but listed as a range**: minor; mostly an intent/format mismatch.

We report per-mode lift and the pooled lift.

### 5.3 File corpus (≥30 files; aim for ≥20 trials/mode/arm after exclusions)

Real files minimize the chance the model "knows" the bytes. Use:

- AuroraCoder's own repo files (`AuroraCoder/src/**/*.py`, `AuroraCoder/frontend/src/**/*.{js,jsx}`), and
- the wider `/workspace` repo files (e.g. `AgentToolStore/`, `examples/`).

Stratify by file size buckets (small ≤50 lines, medium 51-300, large 301-1000) and language (Python, JS/JSX, Markdown) so reporting can split by these. Each (file × mode) pair is one candidate trial; we draw a probe task per file.

### 5.4 Probe task design (so it is solvable from current file state)

Probe edit is a single, unambiguous edit expressible in one canonical edit:

- Rename a unique identifier on a single line (single-line edit).
- Insert a docstring/return line into an existing block (multi-line, requires `[TO]`).
- Delete a small unique block (range delete, `replace_content=""`).

Tasks are auto-generated from the file via a deterministic mutator: pick a unique line, compute its ground-truth canonical `edit_file` call (this is our oracle for §6.3 "did the model say the right thing"); the NL instruction is a templated description ("rename `foo` to `bar` on the line containing ..."). The *model never sees* the oracle canonical call.

## 6. Procedure

### 6.1 Hunches are run through the *real* loop, not a mock

We use AuroraCoder's own provider call path (`providers.py` / `main_flow.py`) so the prompt construction (system prompt with `edit_file` schema, tool definitions, code-interpreter panel) is identical to production. We **call the LLM once** per probe (single-turn continuation from a seeded history). One LLM call per arm per trial keeps cost bounded and the comparison clean (we measure the *next* edit's format, not a multi-turn trajectory).

### 6.2 Building the seeded history for a trial (identical except the one tool_call slot)

Messages handed to the loop:

1. `role:system` — production system prompt + tool defs (unchanged).
2. `role:user` — "Open `<file>` and study it." 
3. `role:assistant` — one `read_file` tool_call (its result auto-populated from the real `read_file_tool`).
4. `role:tool` — the read result.
5. `role:assistant` — the **seeded wrong prior `edit_file` tool_call**. **← this is the only thing that differs between arms.**
   - Arm A: arguments = the malformed edit (verbatim from §5.2).
   - Arm B: arguments = the canonical `corrected_edit` produced by running the engine against the ground-truth intended range.
6. `role:tool` — the tool result. **We fix this to be *identical text* in both arms in the main comparison** ("⚠️ Original parameters were auto-corrected... ✅ Applied...") so that the *only* difference is the assistant-side tool_call text and not the *error text*. (See §6.4 for the secondary comparison that un-fixes the result text to attribute effect to "error vs. success narrative".)
7. `role:user` — the **probe task** (§5.4) + the standard reminder the production loop appends.

The loop then makes exactly **one** LLM call and we capture the emitted assistant tool_calls.

### 6.3 Measurement

For that captured assistant message: take the first `edit_file` tool_call (if the model emits non-edit tools first, count it as a separate `detour` outcome but still measure the eventual `edit_file`). Run the deterministic format judge (§4.3). Record all metrics in §4.2 + mode + file-bucket.

### 6.4 Secondary comparison to disentangle "rewrite" from "error-vs-success framing"

Because Arm B in §6.2 forces the *success* result text in both arms, the measured lift isolates **seeing a canonical prior call** vs **seeing a malformed prior call** — the cleanest test of H1. We run a second arm **B'** (`rewrite_template + real_error_text`) where step 6 is the real anchor-not-found error for both A and B', to test that the lift is *not* solely from the success/error narrative ("⚠️ auto-corrected… no action needed"). If B > A but B' ≈ A, the effect is the *narrative* not the template (interesting but weaker claim). If both B and B' > A, the **template itself** carries the effect → supports H1 strongly.

## 7. Controls & sample size

- **Models**: run on ≥3 models spanning capability (e.g. one GPT-class, one Claude-class, one open-weight) to test generality; report per-model and pooled. Reuse `providers.py`.
- **Randomization**: order of trials shuffled; arms interleaved; seeds cycled.
- **Temperature**: fixed `T=0` (or per-model greedy) for the *probe* call so format adherence is the signal not sampling noise. Optionally repeat each trial at `T∈{0,0.3,0.7}` to measure a *temperature × treatment* interaction (does rewrite help more at higher T where the wrong-history pull is stronger?).
- **Sample size**: ≥20 trials per (mode × arm) for the primary comparison; with binary outcome and assuming A≈0.35, B≈0.65, n=20/arm gives ~80% power at α=0.05 (Fisher exact / two-proportion z). Pre-register this before running.
- **Exclusions**: trials where the probe call is not an `edit_file` (model refuses / calls another tool) are recorded separately; report exclusion rate per arm (an exclusion-rate difference is itself a finding). Do not drop silently.

## 8. Analysis plan

Primary endpoint: `format_correct` for the probe call.
- Two-sample test: per-mode and pooled. Categorical modeler is a logistic mixed-effects model:
  `format_correct ~ arm + mode + size_bucket + language + (1|file) + (1|model)`,
  reporting odds-ratio of `arm` (rewrite vs no-rewrite) and its 95% CI + p-value. `file` and `model` as random effects account for the paired structure (same file/mode appears in both arms) and cross-model generalization.
- Effect sizes: report absolute lift `(B−A)` and relative `(B/A)` per mode, with CIs.
- Break-down: `syntax_correct` vs `anchor_resolvable` to localize where the wrong-history contaminates the model (do models mostly forget `[TO]`? mostly drift line numbers? mostly add stray keys?). This decomposition is the most actionable output for the team (tells us *what* the rewrite is teaching).
- Pre-registered thresholds for H1: pooled OR lower 95% bound > 1, and absolute lift ≥ 0.10.

## 9. Threats to validity & mitigations

| Threat | Mitigation |
|---|---|
| Effect is just "success/error framing", not the template itself. | The `B'` arm (§6.4) with matched error text. |
| Model "knows" the file bytes. | Use repo files it likely wasn't trained on; also run a held-out version with identifier renaming (mangled names) to confirm effect persists. |
| Over-fitting to one failure mode. | Pool across 5 modes (§5.2); report per-mode. |
| The rewrite-template (Arm B) leaks ground truth (the "right edit"). | Arm B's seeded call is the *prior corrupt* edit corrected to canonical form, **not** the probe edit's oracle — different line region/intent; probe is independently solvable. |
| Greedy T=0 makes both arms near-deterministic ceiling. | Add higher-T runs; the rewrite benefit should grow with T. |
| Arm A's "wrong" args are themselves an inconsistent target. | Standardize per-mode (§5.2) with hand-authored templates and write them as data, not ad-hoc. |
| File state mutated by the seeded edit history. | The seeded call is never actually applied to the disk; only its *text* is in history. The real file on disk before the probe is identical across arms. |

## 10. Implementation plan (reuse AuroraCoder internals, minimal new code)

New module `AuroraCoder/evals/tool_call_rewrite_eval/`:

1. `corpus.py` — walks the repo, picks files, generates (file_state, probe_task, probe_oracle) triplets via a deterministic mutator.
2. `wrong_edits.py` — the §5.2 malfunction templates and a builder that, given (file, mode, intended_range), emits both the Arm-A malformed args and (by running `RangeReplaceEditor._validate_one_edit` against the intended range) the Arm-B canonical args.
3. `harness.py` — assembles the seeded message history (§6.2) per arm, invokes the real provider call (one call), captures the probe assistant tool_calls. Reuses `providers.py` and the production system prompt + tool definitions so prompt parity is automatic.
4. `judge.py` — the deterministic format judge (§4.3) wrapping `RangeReplaceEditor._validate_one_edit` + `_parse_line_range` + the `[TO]` parse, returning §4.2 metrics.
5. `run_eval.py` — orchestrates modes × arms × files × models × temperatures, writes a JSONL per trial, then produces the §8 stats (uses `pandas`/`statsmodels` or simple pure-Python exact tests to avoid new heavy deps).
6. `report.py` — emits a markdown table: per-mode and pooled lift, OR + CI, decomposed `syntax` vs `anchor` deltas, per-model, per-temperature.

Reused verbatim (no edits to production behavior): `RangeReplaceEditor`, `find_anchor_tolerant`, `_parse_line_range`, `execute_edit_file`, `tool_definitions.execute_tool_call`, `providers.py`.

Sanity gates before trusting numbers:
- On F1 (engine can already auto-fix), Arm-A's *real* applied form (if we let history reflect the engine output) must equal our Arm-B template → confirms our `corrected_edit` builder matches the engine.
- A trivial "seeds" arm where step-5 history is *already a perfect canonical call* in both A and B → format_correct ≈ identical across A and B (ceiling), confirming the harness isn't leaking arm identity anywhere else.

## 11. What success looks like / decision rule

- If pooled `format_correct` lift **B − A ≥ 0.10** with OR lower-95% > 1 across models and temperatures, and the decomposition shows the lift concentrated in the template-specific violations (F3 `[TO]`, F4 stray keys) rather than the already-auto-fixable F1: **conclude the tool-call rewrite innovation is a meaningful in-context teaching signal worth keeping/investing in**, not just a correctness-of-the-current-call fix.
- If lift is concentrated in `B > A but B' ≈ A`: rewrite's value is *narrative/emotional* (model sees "it worked"), not template-demonstration — different, weaker product claim.
- If lift ≈ 0: keep the rewrite for its immediate correctness value, but stop claiming downstream teaching benefit.

## 12. Out of scope (for this round)

- Multi-turn trajectories / compounding contamination across many edits (future: "how does a chain of wrong vs rewritten calls compound?").
- Non-`edit_file` tools (the same mechanism touches any tool whose `applied` is normalized; extending to e.g. schema-stripped tool calls is future work).
- Comparing against competitor agents (aider, etc.) — we isolate AuroraCoder's own two behaviors.

## 13. Timeline (suggested)

- Day 1: build `corpus.py` + `wrong_edits.py` + `judge.py` on local files; validate sanity gates on 5 files × 5 modes.
- Day 2: harness + one-model dry run (small N) to confirm metrics non-degenerate and arm-balance of exclusions.
- Day 3-4: full run (≥3 models × 5 modes × ≥20 files × {T0, T0.3, T0.7}).
- Day 5: stats + report (§8, §11) + reproducible artifact dump.