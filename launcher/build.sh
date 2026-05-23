#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# Build the AuroraCoder one-click launcher binary
#
# This script:
#   1. Copies the project files into launcher/embed/ (excluding build artifacts)
#   2. Builds the Go binary for the current platform
#   3. Output: thinkwithtool-launcher (or .exe on Windows)
#
# For cross-compilation (all platforms), use:
#   ./build.sh --all
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$SCRIPT_DIR"

OUTPUT_NAME="thinkwithtool-launcher"
EMBED_DIR="$SCRIPT_DIR/embed"

# ── Clean & prepare embed directory ──────────────────────────────────────────
echo "═══ Preparing embed directory..."
rm -rf "$EMBED_DIR"
mkdir -p "$EMBED_DIR"

# Copy project files, excluding build artifacts and large directories
echo "  Copying project files..."
rsync -av \
    --exclude='.git' \
    --exclude='node_modules' \
    --exclude='dist' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --exclude='data' \
    --exclude='workspace' \
    --exclude='sessions' \
    --exclude='.env' \
    --exclude='launcher' \
    --exclude='docs' \
    --exclude='*.egg-info' \
    --exclude='.mypy_cache' \
    --exclude='.pytest_cache' \
    --exclude='.search_cache' \
    --exclude='*.log' \
    --exclude='.cursor' \
    --exclude='.idea' \
    --exclude='.vscode' \
    --exclude='*.swp' \
    --exclude='*.swo' \
    --exclude='*.map' \
    --exclude='.github' \
    "$PROJECT_DIR/" "$EMBED_DIR/" 2>&1 | tail -3

echo "  ✅ Embed directory prepared."

# ── Build ────────────────────────────────────────────────────────────────────
VERSION="${VERSION:-$(date +%Y.%m.%d)}"
LDFLAGS="-s -w -X main.version=${VERSION}"

build_for() {
    local os="$1"
    local arch="$2"
    local ext="$3"
    local out="${OUTPUT_NAME}-${os}-${arch}${ext}"

    echo ""
    echo "═══ Building for ${os}/${arch}..."
    GOOS="$os" GOARCH="$arch" CGO_ENABLED=0 go build \
        -trimpath \
        -ldflags "$LDFLAGS" \
        -o "$out" \
        .
    echo "  ✅ $out ($(du -h "$out" | cut -f1))"
}

if [ "${1:-}" == "--all" ]; then
    echo ""
    echo "══════════════════════════════════════════════"
    echo "  Building for all platforms..."
    echo "══════════════════════════════════════════════"

    build_for linux   amd64   ""
    build_for linux   arm64   ""
    build_for darwin  amd64   ""
    build_for darwin  arm64   ""
    build_for windows amd64   ".exe"

    echo ""
    echo "══════════════════════════════════════════════"
    echo "  All builds complete!"
    echo "══════════════════════════════════════════════"
    ls -lh ${OUTPUT_NAME}-*
else
    echo ""
    echo "═══ Building for current platform..."

    # Detect current platform
    case "$(uname -s)" in
        Linux*)  GOOS="linux" ;;
        Darwin*) GOOS="darwin" ;;
        CYGWIN*|MINGW*|MSYS*) GOOS="windows" ;;
        *)       GOOS="linux" ;;
    esac

    ARCH="$(uname -m)"
    case "$ARCH" in
        x86_64|amd64) GOARCH="amd64" ;;
        aarch64|arm64) GOARCH="arm64" ;;
        *) GOARCH="amd64" ;;
    esac

    EXT=""
    if [ "$GOOS" == "windows" ]; then
        EXT=".exe"
    fi

    CGO_ENABLED=0 go build \
        -trimpath \
        -ldflags "$LDFLAGS" \
        -o "${OUTPUT_NAME}${EXT}" \
        .

    echo "  ✅ ${OUTPUT_NAME}${EXT} ($(du -h "${OUTPUT_NAME}${EXT}" | cut -f1))"
    echo ""
    echo "══════════════════════════════════════════════"
    echo "  Build complete!"
    echo "  Binary: ${SCRIPT_DIR}/${OUTPUT_NAME}${EXT}"
    echo "══════════════════════════════════════════════"
fi
