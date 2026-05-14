# Frontend CSS Audit — Issues Found During Monolith Split

> Generated during the `index.css` → 11 domain file split.

---

## 1. 🔴 Undefined CSS Variables (Runtime Bugs)

These CSS variable names are referenced in stylesheets but **never defined** in `tokens.css`.  
The browser silently falls back to `unset`, which means these rules have **no visible effect** — the properties render as if unset.

| Variable | Referenced In | Occurrences | Fallback Behavior |
|----------|---------------|:-----------:|-------------------|
| `--accent-color` | `sidebar.css` | 4 | With fallback `var(--accent-color, var(--text-primary))`, the fallback always kicks in — so the accent-colored state (`.selected`, `.active`) just shows default text color. |
| `--border-color` | `sidebar.css`, `messages.css` | 3 | **No fallback.** The `border`/`border-top` properties resolve to `unset`, meaning **no border renders at all**. |
| `--accent-text` | `messages.css` | 1 | **No fallback.** The `.retry-btn:hover` text color resolves to `inherit`, losing the intended hover contrast. |

**Affected selectors:**
- `.current-session-main.selected` — missing accent color
- `.current-session-child.selected` — missing accent color
- `.subagent-back-btn` — missing border + accent color
- `.subagent-view-link` — missing accent color
- `.subagent-view-bar` — missing top border
- `.sender-label` — missing accent color
- `.retry-btn:hover` — missing text color on hover

### Fix

Either define these variables in `tokens.css`:

```css
:root {
  --accent-color: var(--accent-primary);
  --border-color: var(--border-default);
  --accent-text: white;
}
```

Or replace each usage with the equivalent token directly (e.g., `--accent-primary`, `--border-default`).

---

## 2. 🟡 Hardcoded Colors Duplicating Design Tokens

Multiple places use raw hex values that duplicate (or nearly duplicate) existing CSS variables. This defeats the purpose of the token system and means these elements won't respond to theme changes or future token redesigns.

| Raw Value | Used Where | Duplicates | Note |
|-----------|-----------|------------|------|
| `#ef4444` | `tool-activity.css` L105,134, `sidebar.css` L523, `file-tree.css` L232,294,296 | `--error: #ef4444` | Exact match — just use `var(--error)` |
| `#d97706` | `sidebar.css` L596,610,613,620 | `--warning: #f59e0b` | Different amber shade — but same semantic purpose |
| `#dc2626` | `file-tree.css` L300,301 | `--error: #ef4444` | Slightly darker red for hover — could be a dedicated token |
| `#22c55e` | `sidebar.css` L501,518,519 | `--success: #10b981` | Different green — used for active status dot |
| `#78350f22` | `sidebar.css` L595 | none | Hardcoded warning-bg with alpha |

**Recommendation:** Move these into `tokens.css` as semantic variables:

```css
:root {
  --status-active: #22c55e;
  --warning-border: #d97706;
  --warning-bg: #78350f22;
  --error-hover: #dc2626;
}
```

Then replace all raw hex usage with `var(--status-active)`, `var(--error)`, etc.

---

## 3. 🟡 Empty CSS Rule (Dead Code)

In `sidebar.css` line 23:

```css
.app.sidebar-collapsed .main-content {
  /* When sidebar is collapsed, main content gets more space */
}
```

This rule has zero CSS properties — just a comment. It was likely a placeholder that was never filled in. It does nothing and should be removed.

---

## 4. 🟡 Missing CSS Class Definition

`Sidebar.jsx` line 94 uses the class `task-instructions-btn`:

```jsx
className="load-session-btn task-instructions-btn"
```

But **no CSS file defines `.task-instructions-btn`**. The element only gets styles because it also has `.load-session-btn`. This means:
- If `.load-session-btn` styles change, there's no way to differentiate the task-instructions button
- It's misleading: the class exists in JSX but has no styling purpose

**Fix:** Either add a `.task-instructions-btn` rule (if differentiation is needed) or remove the unused class from JSX.

---

## 5. ✅ Already Fixed: Duplicate `@keyframes`

The original `index.css` had **three** separate `@keyframes spin` definitions:

| Location (old) | Definition |
|----------------|-----------|
| ~line 1787 | `transform: rotate(-360deg)` — counter-rotating |
| ~line 2296 | `transform: rotate(360deg)` — standard |
| ~line 2898 | `transform: rotate(360deg)` — duplicate of 2296 |

**Resolution:** All `@keyframes` were consolidated into `tokens.css` during the split, with the standard `360deg` spin kept and the anomalous `-360deg` dropped. All animation *usage* remains in the domain files — only the definitions were deduplicated.

---

## 6. 🟢 `!important` Overrides (Acceptable)

4 `!important` declarations exist, all in `messages.css` lines 175–181, targeting syntax-highlighter elements:

```css
.message-text div[class*="language-"] {
  border-radius: var(--radius-md) !important;
  margin: 1em 0 !important;
}
.message-text pre[class*="language-"] {
  margin: 0 !important;
  padding: 1em !important;
}
```

This is **reasonable** — these override inline styles injected by `react-syntax-highlighter`, which can't be targeted otherwise. No action needed.

---

## 7. 🟢 `sidebar.css` Still Large (869 lines)

After the split, `sidebar.css` remains the largest single file because it covers many sub-features:

- Sidebar chrome (toggle, logo, theme btn)
- New chat + session picker buttons
- Conversation history (current session + full drawer)
- Task instructions drawer
- Subagent view bar
- Active conversation warning
- Sidebar footer (model selector, provider dropdown)
- System prompt input

**Recommendation:** If the sidebar continues to grow, consider splitting it further into:
```
styles/sidebar/
├── sidebar-chrome.css
├── sidebar-conversations.css
├── sidebar-drawers.css
└── sidebar-footer.css
```
But this is low-priority — 869 lines is manageable for now.

---

## Priority Summary

| # | Issue | Severity | Effort |
|---|-------|----------|--------|
| 1 | Undefined `--border-color` (no fallback) | 🔴 Bug | 5 min |
| 1 | Undefined `--accent-text` (no fallback) | 🔴 Bug | 5 min |
| 1 | Undefined `--accent-color` (has fallback) | 🟡 Cosmetic | 5 min |
| 2 | Hardcoded colors duplicating tokens | 🟡 Maintainability | 20 min |
| 3 | Empty CSS rule (dead code) | 🟢 Cleanup | 1 min |
| 4 | Missing `.task-instructions-btn` class | 🟡 Confusing | 2 min |
| 5 | Duplicate `@keyframes` | ✅ Fixed | — |
| 6 | `!important` overrides | ✅ OK | — |
| 7 | `sidebar.css` still large | 🟢 Low priority | — |

**Total to fix:** ~30 minutes for all 🔴 and 🟡 items.
