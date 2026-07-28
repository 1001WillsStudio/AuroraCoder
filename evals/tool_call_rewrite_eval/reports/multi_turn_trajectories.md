# Tool-Call Rewrite — Multi-Turn Trajectory Evaluation

**4 tool calls per trajectory; the first (T1) is designed by us.**

T1 = SEED (by design): Arm-B history carries the canonical `[TO]` template; Arm-A history carries the model's own no-`[TO]` full-block template. Both apply with a clean success narrative + identical post-seed file state. So the ONLY difference across arms is the TEMPLATE of each prior assistant tool_call.

T2..T4 = the AGENT's own follow-up edits, one per disjoint line-neutral region. We judge each emitted call's format (`used_right_template` = right canonical template).

The KEY question: **does the rewrite's teaching effect persist / grow / fade across turns 2→4?**


Complete trajectories kept: B=45, A=45


## Temperature = 0.7  (B=45, A=45 trajectories)

### Per-turn `used_right_template` rate (the trace table)

| Turn | role | Arm-B rate | Arm-A rate | paired diff (B-A) | 95% CI | n |
|---|---|---|---|---|---|---|
| T1 | seed | 100.0% | 0.0% | +1.000 | [+1.000, +1.000] | 45 |
| T2 | agent | 51.1% | 35.6% | +0.156 | [-0.067, +0.356] | 45 |
| T3 | agent | 77.8% | 28.9% | +0.489 | [+0.333, +0.644] | 45 |
| T4 | agent | 93.3% | 22.2% | +0.711 | [+0.578, +0.822] | 45 |

### Per-turn `[TO]` usage in the emitted call (does the rewrite propagate `[TO]`?)

| Turn | Arm-B `[TO]`% | Arm-A `[TO]`% | paired diff (B-A) | 95% CI |
|---|---|---|---|---|
| T1 | 100.0% | 0.0% | +1.000 | [+1.000, +1.000] |
| T2 | 51.1% | 35.6% | +0.156 | [-0.067, +0.356] |
| T3 | 75.6% | 28.9% | +0.467 | [+0.311, +0.622] |
| T4 | 91.1% | 22.2% | +0.689 | [+0.556, +0.822] |

### Per-turn `emit_applied` rate (did the emitted edit apply, even if non-canonical?)

| Turn | Arm-B applied% | Arm-A applied% | diff |
|---|---|---|---|
| T1 | 100.0% | 100.0% | +0.000 |
| T2 | 100.0% | 100.0% | +0.000 |
| T3 | 97.8% | 100.0% | -0.022 |
| T4 | 100.0% | 100.0% | +0.000 |

### Trajectory-shape categories (agent turns T2..T4 of `used_right_template`)

Pattern of the 3 agent turns (✓=right template, ✗=wrong): each cell = number of trajectories.

| pattern (T2,T3,T4) | Arm-B count | Arm-A count |
|---|---|---|
| (✓,✓,✓) | 20 | 10 |
| (✓,✓,✗) | 1 | 3 |
| (✓,✗,✓) | 2 | 0 |
| (✓,✗,✗) | 0 | 3 |
| (✗,✓,✓) | 14 | 0 |
| (✗,✗,✓) | 6 | 0 |
| (✗,✗,✗) | 2 | 29 |

- **Recovery** = T2 wrong template → T3 or T4 right template (does the arm self-learn the canonical form despite starting from a wrong call?).
- **Degradation** = T2 right template → T3 or T4 wrong template (does the arm drift OFF the canonical form after starting well?).
  - Recovery (T2 wrong → later right):   B=20/45,  A=0/45
  - Degradation (T2 right → later wrong): B=3/45,  A=6/45

  > **Headline:** Arm-A (no rewrite) NEVER self-recovers the canonical template (0/45) once it starts wrong.

## Long-term behavior verdict

- At T2 (turn right after the seed): B-A diff = **+0.156**, 95% CI [-0.067, +0.356], n=45
- At T4 (last agent turn): B-A diff = **+0.711**, 95% CI [+0.578, +0.822], n=45
- B-A teaching gap across agent turns T2..T4: **+0.16 → +0.49 → +0.71** (compounds across turns)

**the rewrite's teaching signal COMPOUNDS and ENDURES to the last agent turn (95% CI excludes zero at every agent turn from T3 on).**