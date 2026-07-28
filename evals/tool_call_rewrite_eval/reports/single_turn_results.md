# Tool-Call Rewrite Evaluation — Results

Flow:<br/>Arm-B uses canonical (rewritten) prior edit_file call + clean success text;<br/>Arm A_clean uses the MALFORMED prior call but with the SAME clean success narrative+file (isolates the **call text**);<br/>Arm A_nat uses the malformed prior call + its NATURAL production result (error/auto-corrected) + actual file outcome.

Data points: 648 rows (model manifest in file names).


## Temperature = 0.7  (n_rows=648)

### Overall rates per arm (only rows where the model emitted an edit_file call)

| Arm | n_emit | used_right_template | format_correct | anchor_resolvable | emit_rate |
|---|---|---|---|---|---|
| B | 216 | 90.3% | 90.3% | 100.0% | 100.0% |
| A_clean | 215 | 84.7% | 84.7% | 99.5% | 99.5% |
| A_nat | 215 | 80.5% | 80.0% | 99.5% | 99.5% |

### Per-mode `used_right_template` rate (cached metric)

| Mode | narrative | n_emit | B | A_clean | A_nat |
|---|---|---|---|---|---|
| F1 | error | 30 | 100.0% | 100.0% | 100.0% |
| F2 | error | 30 | 100.0% | 100.0% | 100.0% |
| F3 | apply | 66 | 68.2% | 50.0% | 36.4% |
| F4 | error | 30 | 100.0% | 100.0% | 100.0% |
| F5 | apply | 30 | 100.0% | 100.0% | 100.0% |
| F6 | apply | 30 | 100.0% | 100.0% | 100.0% |

### [TO] usage (F3 multi-line probes only) — does the prior call teach [TO]?

| Arm | n_emit | uses_[TO] rate |
|---|---|---|
| B | 66 | 68.2% |
| A_clean | 66 | 50.0% |
| A_nat | 66 | 36.4% |

### Paired comparison — PRIMARY (B vs A_clean), metric=used_right_template

- n paired trials = 215
- B-right & A-wrong (rewrite wins): **21**
- B-wrong & A-right (rewrite loses): **9**
- mean per-trial diff (B - A_clean) = **+0.056** &nbsp; (positive = rewrite helps)
- bootstrap 95% CI for the diff: **[+0.005, +0.107]**
- McNemar χ² = 4.03, p = 0.0446
- Verdict: **SUPPORTS** the tool-call-rewrite teaching hypothesis.

### Paired comparison — SECONDARY (B vs A_nat), metric=used_right_template

- n paired trials = 215
- B-right & A_nat-wrong: 28  |  B-wrong & A_nat-right: 7
- mean per-trial diff (B - A_nat) = +0.098  95% CI [+0.047, +0.149]
- McNemar χ² = 11.43, p = 0.0007

### Banner/narrative decomposition (A_clean vs A_nat)

Holding the malformed prior CALL fixed, this compares ONLY the tool result narrative: A_clean uses the clean success text; A_nat uses the genuine production text (apply modes → the "⚠️ auto-corrected" banner + success; error modes → the error string + unchanged file). A positive (A_clean - A_nat) diff means the clean narrative yields MORE canonical follow-up calls than the auto-correct banner.

| Group | n_pairs | mean diff (A_clean - A_nat) | 95% CI | McNemar p | readout |
|---|---|---|---|---|---|
| ALL | 214 | +0.042 | [+0.000, +0.089] | 0.1096 | no clear effect |
| F3 (apply / banner present) | 66 | +0.136 | [-0.015, +0.273] | 0.1096 | no clear effect |
| error modes (F1/F2/F4) | 89 | +0.000 | [+0.000, +0.000] | 1.0000 | no clear effect |

Interpretation: if A_clean > A_nat on F3 (where the auto-correct banner is what A_nat actually shows), it suggests the "⚠️ auto-corrected, no action needed" banner may *undermine* the teaching signal — it tells the model its bad params 'didn't matter', reducing the pressure to adopt the canonical form itself.

### Per-mode PRIMARY (B vs A_clean) paired diffs (positive = rewrite helps)

| Mode | narrative | n_pairs | mean diff | 95% CI | McNemar p |
|---|---|---|---|---|---|
| F1 | error | 30 | +0.000 | [+0.000, +0.000] | 1.0000 |
| F2 | error | 30 | +0.000 | [+0.000, +0.000] | 1.0000 |
| F3 | apply | 66 | +0.182 | [+0.015, +0.333] | 0.0446 |
| F4 | error | 30 | +0.000 | [+0.000, +0.000] | 1.0000 |
| F5 | apply | 30 | +0.000 | [+0.000, +0.000] | 1.0000 |
| F6 | apply | 29 | +0.000 | [+0.000, +0.000] | 1.0000 |

## Verdict summary


Overall `used_right_template` rate: B = 90.3%, A_clean = 84.7%, paired mean diff (B-A_clean) = +0.056, 95% CI [+0.005, +0.107].

### **SUPPORTS** — the silent tool-call rewrite measurably increases the rate at which the agent's NEXT edit_file call uses the canonical template.