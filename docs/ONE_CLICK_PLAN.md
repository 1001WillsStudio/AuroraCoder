# AuroraCoder — One-Click Launcher Plan

## Goal

User downloads **one file**, double-clicks it, and AuroraCoder is fully running.
No git clone, no terminal, no Node.js, no Python — only Docker Desktop required.

---

## Core Idea

A **Go binary (~2-3 MB)** with the entire project source embedded.
On launch it extracts to a cache dir, builds the Docker image, starts the container, and opens the browser.

```
auroracoder.exe  ←  you build this once, ship it
  ├── Dockerfile
  ├── Dockerfile.base
  ├── frontend/         (source, not node_modules)
  ├── src/
  ├── conversation_gateway/
  └── ...all project files embedded at compile time
```

---

## Required Change: Frontend Moves Into Docker

Right now the frontend dev server runs on the host (needs Node.js). To eliminate that dependency,
the frontend must be **built inside Docker** and served by the existing gateway.

| What Changes | File(s) |
|---|---|
| `npm install && npm run build` during `docker build` | `Dockerfile` |
| Gateway serves `frontend/dist/` as static files on `:8081` | `conversation_gateway/api.py` |
| Remove host-side frontend step from start scripts | `start.bat`, `start.sh` |

After this, opening `http://localhost:8081` gives the full app — no separate Vite server.

---

## Launcher Binary

- **Language:** Go (stdlib `embed`, tiny binary, trivial cross-compilation)
- **Size:** ~2-3 MB
- **Behavior:**

  ```
  ┌─────────────────────────────────────────┐
  │  Double-click auroracoder.exe           │
  │                                         │
  │  1. Extract project to cache dir        │
  │  2. Check Docker is running             │
  │  3. Show progress window                │
  │     "Setting up AuroraCoder..."         │
  │  4. docker build -t thinkwithtool ...   │
  │     (10-15 min first time, cached after)│
  │  5. docker run ... (backend+gateway+VNC)│
  │  6. Open http://localhost:8081          │
  │  7. Minimize to system tray             │
  └─────────────────────────────────────────┘
  ```

- **Build command (one line, all platforms):**
  ```bash
  GOOS=windows go build -o auroracoder.exe
  GOOS=darwin  go build -o auroracoder-mac
  GOOS=linux   go build -o auroracoder-linux
  ```

---

## User Experience

| | First Launch | Subsequent Launches |
|---|---|---|
| **Time to running** | 10-15 min (image build) | ~10 seconds (cache hit) |
| **What they see** | Small progress window with Docker build output | Progress window, then browser opens |
| **Clicks required** | 1 (double-click) | 1 (double-click) |

---

## Trade-Off to Consider

**Pre-built Docker image vs. Build on first launch**

| | Build on first launch | Embed pre-built image |
|---|---|---|
| **Binary size** | ~2-3 MB | ~2-3 GB |
| **First launch** | 10-15 min | Instant |
| **Complexity** | Simple | Needs CI to build + embed tarball |
| **Recommendation** | **Start here** | Add later if needed |

Start with build-on-first-launch. If user feedback says the wait is unacceptable,
add a CI pipeline that pre-builds the image and produces a "fat" binary.

---

## Scope Summary

| Task | Effort |
|---|---|
| Move frontend into Docker (`Dockerfile` + gateway static serving) | Small |
| Go launcher binary (extract, docker build, docker run, open browser, tray) | Medium |
| Cross-compile & release workflow (GitHub Actions / goreleaser) | Small |
| Progress GUI (optional — could just be terminal window with output) | Small |

---

## Open Questions

1. **Progress UI:** A small GUI window (Go + walk/fyne), or just a terminal with live output?
2. **Auto-update:** Should the launcher check for new versions on startup?
3. **Platform specifics:** macOS needs `.app` bundle (not a raw binary). Windows needs code-signing to avoid SmartScreen warnings.
4. **Keep start.sh/start.bat?** Yes — for developers who clone the repo and want the non-embedded workflow.
