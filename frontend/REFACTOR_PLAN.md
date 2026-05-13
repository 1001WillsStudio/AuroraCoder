# Frontend Refactoring Plan

## Current Problem

`App.jsx` is ~1465 lines / 57KB — a "God Component" that handles all state, effects, business logic, and layout rendering. This makes it difficult to maintain, test, and reason about.

## Target Architecture

```
src/
├── App.jsx                      (~200 lines — thin orchestrator)
├── main.jsx
├── components/
│   ├── ChatMessage.jsx
│   ├── ChatInput.jsx            ← NEW: extracted input area
│   ├── Sidebar.jsx              ← NEW: full sidebar composition
│   ├── CodePanel.jsx
│   ├── FileTree.jsx
│   ├── SessionPicker.jsx
│   ├── ConversationHistory.jsx
│   ├── WelcomeScreen.jsx
│   ├── SubagentViewBar.jsx      ← NEW: subagent read-only bar
│   └── ActiveConvoWarning.jsx   ← NEW: "agent still running" warning
├── hooks/
│   ├── useChat.js               ← NEW: stream/continue/stop/retry logic
│   └── useFileTracking.js       ← NEW: file diff polling, auto-close
├── services/
│   └── api.js
├── utils/
│   └── streamUtils.js           ← NEW: isInterruptible, constants
└── styles/
    └── index.css
```

## Refactoring Steps

### Phase 1: Extract Utilities (low risk, immediate win)
1. **`utils/streamUtils.js`** — Move `isInterruptible()`, `CODE_TOOLS`, `FILE_SYSTEM_TOOLS`, `TASK_MARKER_START/END`

### Phase 2: Extract Custom Hooks
2. **`hooks/useFileTracking.js`** — File diff polling, `editedFiles`/`closedFiles` state, auto-close code panel effect, file tree refresh tracking
3. **`hooks/useChat.js`** — All streaming logic: `handleSend`, `handleInterruptSend`, `handleContinue`, `handleStop`, `handleStopTool`, `handleRetry`, abort controller management

### Phase 3: Extract Components
4. **`components/ChatInput.jsx`** — Input textarea + send/stop/interrupt buttons + hint text (4 visual modes)
5. **`components/Sidebar.jsx`** — Logo, theme toggle, sidebar toggle, new chat, load session, upload, session info, task instructions, file tree, conversation history, model selector
6. **`components/SubagentViewBar.jsx`** — "Subagent running / read-only" bar with back button
7. **`components/ActiveConvoWarning.jsx`** — Warning banner when another conversation is active

### Phase 4: Slim Down App.jsx
8. Wire everything together in a thin App.jsx that only orchestrates layout

---

## Future Consideration: Interrupt/Resume State Machine

### The Problem
`handleSend` currently calls itself recursively via `setTimeout` in the `onMessages` callback:

```
handleSend()
  └─ streamChat()
       └─ onMessages()
            └─ detects safe state + pending interrupt
                 └─ aborts stream → setTimeout → handleSend()  ← recursion
```

This is fragile for several reasons:
- **Hard to trace**: the call chain is self-referential and spans async boundaries
- **Dual state tracking**: `pendingInterrupt` exists as both `useState` (UI) and `useRef` (closure-safe)
- **Race conditions**: abort-then-restart with 50ms delay relies on timing assumptions

### Proposed Solution: Explicit State Machine
Replace the recursive call pattern with explicit states:

```
States:
  idle → streaming → interrupt-pending → restarting → streaming
                                                 ↘ error → idle
```

| State | Description |
|-------|-------------|
| `idle` | No active stream, input enabled |
| `streaming` | SSE stream active, messages arriving |
| `interrupt-pending` | User sent interrupt, waiting for safe tool-completion point |
| `restarting` | Safe point reached, aborting old stream, about to start new one |
| `error` | Stream failed, retry possible |

A single `useChat` reducer would manage transitions, making the flow testable and debuggable.

---

## CSS (deferred)

`index.css` is also 67KB. This should be addressed separately — potentially by co-locating styles with components (CSS modules) or adopting Tailwind.
