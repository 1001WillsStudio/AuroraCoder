# AuroraCoder One-Click Launcher

A **single binary** that deploys AuroraCoder (formerly ThinkWithTool) on any platform with Docker installed. Download, double-click, done.

## Features

- **Zero dependencies** — the binary bundles the entire project. No git, Python, or Node.js required on your machine.
- **Live progress UI** — a browser-based progress page shows real-time Docker build output via SSE, with step-by-step status and auto-redirect when ready.
- **Docker install guide** — if Docker isn't found, the launcher prints OS-specific step-by-step installation instructions (Windows, macOS, Ubuntu/Debian, Fedora, Arch).
- **Cross-platform** — Windows (.exe), macOS (Intel + Apple Silicon), Linux (amd64 + arm64).
- **Small binary** — ~7 MB, embeds all project source files (~1.3 MB); Docker handles the rest.

## How it works

1. **Download** the binary for your platform from [releases](https://github.com/thinkwithtool/thinkwithtool/releases)
2. **Double-click** (or run from terminal)
3. The launcher:
   - Checks Docker is running (or prints install guide)
   - Opens a browser progress page at `http://localhost:8089`
   - Extracts the embedded project to a cache directory
   - Builds Docker images (first time takes a few minutes; cached thereafter)
   - Streams live build output to the progress page
   - Starts the container
   - Auto-redirects to `http://localhost:8081`
4. **Done!** AuroraCoder is running.

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop) (or Docker Engine + Docker Compose)
- Docker daemon must be **running**

If Docker isn't installed, the launcher will print detailed installation instructions for your OS.

## Download

Grab the latest binary from [GitHub Releases](https://github.com/thinkwithtool/thinkwithtool/releases):

| Platform              | Binary                                     |
| --------------------- | ------------------------------------------ |
| Windows (x86_64)      | `thinkwithtool-launcher-windows-amd64.exe` |
| macOS (Intel)         | `thinkwithtool-launcher-darwin-amd64`      |
| macOS (Apple Silicon) | `thinkwithtool-launcher-darwin-arm64`      |
| Linux (x86_64)        | `thinkwithtool-launcher-linux-amd64`       |
| Linux (ARM64)         | `thinkwithtool-launcher-linux-arm64`       |

On macOS/Linux, make executable: `chmod +x thinkwithtool-launcher-*`

## API Keys

On first run, a `.env` file is created from the template. Edit it with your API keys:

| OS      | Path                                                       |
| ------- | ---------------------------------------------------------- |
| macOS   | `~/Library/Caches/ThinkWithTool/launcher-cache/.env`      |
| Linux   | `~/.cache/thinkwithtool/launcher-cache/.env`              |
| Windows | `%APPDATA%\ThinkWithTool\launcher-cache\.env`             |

Then restart the launcher.

## Data Storage

Conversation data and workspace files are persisted in `Documents/ThinkTool/` (or `~/ThinkTool/` as fallback).

## Ports

| Port | Service        |
| ---- | -------------- |
| 8080 | Backend API    |
| 8081 | Frontend App   |
| 6080 | VNC Desktop    |
| 8089 | Progress Page  |
| 8900-8902 | Dev servers |

## Build from source

```bash
# Requires Go 1.24+ and rsync
cd launcher
./build.sh          # Build for current platform
./build.sh --all    # Cross-compile for all 5 platforms
```

## Release workflow

Push a tag matching `v*` and GitHub Actions cross-compiles all binaries, attaching them to a draft release.
