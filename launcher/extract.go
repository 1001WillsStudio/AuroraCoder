package main

import (
	"crypto/sha256"
	"embed"
	"encoding/hex"
	"fmt"
	"io"
	"io/fs"
	"net"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
)

// ─── Embedded project ──────────────────────────────────────────────────────

//go:embed all:embed
var projectFS embed.FS

// ─── Constants ─────────────────────────────────────────────────────────────

const (
	baseImageName = "auroracoder-base"
	appImageName  = "auroracoder"
	containerName = "auroracoder-agent"
)

// ─── Port configuration ─────────────────────────────────────────────────────

// PortsConfig holds the resolved port mappings for the container.
type PortsConfig struct {
	Frontend     int
	Backend      int
	VNC          int
	ToolStore    int
	DevPortStart int
	DevPortEnd   int
}

// defaultPorts returns the hard-coded default port assignments.
func defaultPorts() PortsConfig {
	return PortsConfig{
		Frontend:     3000,
		Backend:      8080,
		VNC:          6080,
		ToolStore:    8765,
		DevPortStart: 8900,
		DevPortEnd:   8902,
	}
}

// readPortsConfig loads ports.conf from the first found location
// (cacheDir, then parent directory). Missing keys fall back to defaults.
func readPortsConfig(cacheDir string) PortsConfig {
	cfg := defaultPorts()

	// Search parent first — user-owned custom config takes priority over
	// the embedded default that extractProject places in cacheDir.
	dirs := []string{}
	parent := filepath.Dir(cacheDir)
	if parent != cacheDir {
		dirs = append(dirs, parent)
	}
	dirs = append(dirs, cacheDir)

	for _, dir := range dirs {
		path := filepath.Join(dir, "ports.conf")
		data, err := os.ReadFile(path)
		if err != nil {
			continue
		}
		for _, line := range strings.Split(string(data), "\n") {
			line = strings.TrimSpace(line)
			if line == "" || strings.HasPrefix(line, "#") {
				continue
			}
			parts := strings.SplitN(line, "=", 2)
			if len(parts) != 2 {
				continue
			}
			key := strings.TrimSpace(parts[0])
			val := strings.TrimSpace(parts[1])
			n, err := strconv.Atoi(val)
			if err != nil || n <= 0 || n > 65535 {
				continue
			}
			switch key {
			case "FRONTEND_PORT":
				cfg.Frontend = n
			case "BACKEND_PORT":
				cfg.Backend = n
			case "VNC_PORT":
				cfg.VNC = n
			case "TOOLSTORE_PORT":
				cfg.ToolStore = n
			case "DEV_PORT_START":
				cfg.DevPortStart = n
			case "DEV_PORT_END":
				cfg.DevPortEnd = n
			}
		}
		break // only read the first found file
	}
	return cfg
}

// isPortAvailable checks whether a TCP port is free by attempting to listen.
func isPortAvailable(port int) bool {
	ln, err := net.Listen("tcp", fmt.Sprintf(":%d", port))
	if err != nil {
		return false
	}
	ln.Close()
	return true
}

// findAvailablePort scans forward from start until a free port is found.
func findAvailablePort(start int) int {
	for port := start; port < start+1000; port++ {
		if isPortAvailable(port) {
			return port
		}
	}
	return start // fallback — hope for the best
}

// findAvailablePortRange finds a contiguous block of width free ports
// starting at or above start. Returns the first port of the block.
func findAvailablePortRange(start, width int) int {
	// Clamp to valid TCP port range
	if start < 1 {
		start = 1
	}
	maxBase := 65535 - width + 1
	if maxBase < 1 {
		maxBase = 1
	}
	limit := start + 10000
	if limit > maxBase {
		limit = maxBase
	}
	for base := start; base <= limit; base++ {
		allFree := true
		for p := base; p < base+width && p <= 65535; p++ {
			if !isPortAvailable(p) {
				allFree = false
				break
			}
		}
		if allFree {
			return base
		}
	}
	return start // fallback
}

// resolvePorts reads the config, auto-finds available ports, and returns
// the resolved PortsConfig. It logs adjustments to the progress server.
func resolvePorts(cacheDir string, ps *progressServer) PortsConfig {
	cfg := readPortsConfig(cacheDir)

	cfg.Backend = findAvailablePort(cfg.Backend)
	cfg.Frontend = findAvailablePort(cfg.Frontend)
	cfg.VNC = findAvailablePort(cfg.VNC)
	cfg.ToolStore = findAvailablePort(cfg.ToolStore)

	width := cfg.DevPortEnd - cfg.DevPortStart + 1
	if width < 1 {
		width = 3
	}
	newStart := findAvailablePortRange(cfg.DevPortStart, width)
	cfg.DevPortStart = newStart
	cfg.DevPortEnd = newStart + width - 1

	return cfg
}

// ─── OS helper ─────────────────────────────────────────────────────────────

func goos() string {
	return runtime.GOOS
}

// ─── Cache directory ──────────────────────────────────────────────────────

func ensureCacheDir() (string, error) {
	var base string

	switch runtime.GOOS {
	case "darwin":
		base = filepath.Join(os.Getenv("HOME"), "Library", "Caches", "AuroraCoder")
	case "windows":
		base = filepath.Join(os.Getenv("APPDATA"), "AuroraCoder")
	default:
		cacheHome := os.Getenv("XDG_CACHE_HOME")
		if cacheHome == "" {
			cacheHome = filepath.Join(os.Getenv("HOME"), ".cache")
		}
		base = filepath.Join(cacheHome, "auroracoder")
	}

	cacheDir := filepath.Join(base, "launcher-cache")
	if err := os.MkdirAll(cacheDir, 0755); err != nil {
		return "", fmt.Errorf("cannot create cache directory %s: %w", cacheDir, err)
	}

	return cacheDir, nil
}

// ─── Extract embedded project ─────────────────────────────────────────────

func extractProject(destDir string) error {
	return fs.WalkDir(projectFS, "embed", func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}

		relPath := strings.TrimPrefix(path, "embed/")
		if relPath == "" {
			return nil
		}

		// Never overwrite user-owned files that may have been customised
		if relPath == ".env" || relPath == "ports.conf" {
			return nil
		}

		destPath := filepath.Join(destDir, relPath)

		if d.IsDir() {
			return os.MkdirAll(destPath, 0755)
		}

		if err := os.MkdirAll(filepath.Dir(destPath), 0755); err != nil {
			return fmt.Errorf("mkdir %s: %w", filepath.Dir(destPath), err)
		}

		src, err := projectFS.Open(path)
		if err != nil {
			return fmt.Errorf("open %s: %w", path, err)
		}
		defer src.Close()

		dst, err := os.Create(destPath)
		if err != nil {
			return fmt.Errorf("create %s: %w", destPath, err)
		}
		defer dst.Close()

		if _, err := io.Copy(dst, src); err != nil {
			return fmt.Errorf("copy %s: %w", path, err)
		}

		info, err := d.Info()
		if err == nil && info.Mode()&0111 != 0 {
			os.Chmod(destPath, info.Mode())
		}

		return nil
	})
}

// ─── .env file ────────────────────────────────────────────────────────────

func ensureEnvFile(cacheDir string, ps *progressServer) bool {
	envPath := filepath.Join(cacheDir, ".env")

	if _, err := os.Stat(envPath); err == nil {
		ps.logLine("✅ .env file found.")
		return false
	}

	// Create a minimal .env — do NOT copy .env.example.
	// .env.example is a user reference, not an application config source.
	// Users set their API keys via the Settings UI in the web interface.
	content := "# AuroraCoder environment configuration\n" +
		"# Set API keys via the Settings UI in the web interface instead.\n"
	if err := os.WriteFile(envPath, []byte(content), 0644); err != nil {
		ps.logLine(fmt.Sprintf("⚠️  Could not create .env: %v", err))
		return true
	}
	ps.logLine("✅ Created minimal .env — set API keys via the Settings UI.")
	return false
}

// ─── ports.conf auto-creation ────────────────────────────────────────────

func ensurePortsConf(cacheDir string, ps *progressServer) {
	path := filepath.Join(cacheDir, "ports.conf")
	if _, err := os.Stat(path); err == nil {
		return // already exists — user may have customised it
	}
	// Also check parent directory (project root) for custom ports.conf
	parentPath := filepath.Join(filepath.Dir(cacheDir), "ports.conf")
	if _, err := os.Stat(parentPath); err == nil {
		ps.logLine("✅ Found ports.conf in project root — using custom port config.")
		return
	}
	content := "# AuroraCoder Port Configuration\n" +
		"# All fields are optional — defaults are used for any missing value.\n" +
		"# The launcher and dev-scripts will auto-find available ports if the\n" +
		"# configured port is already in use by another application.\n" +
		"FRONTEND_PORT=3000\n" +
		"BACKEND_PORT=8080\n" +
		"VNC_PORT=6080\n" +
		"TOOLSTORE_PORT=8765\n" +
		"DEV_PORT_START=8900\n" +
		"DEV_PORT_END=8902\n"
	if err := os.WriteFile(path, []byte(content), 0644); err != nil {
		ps.logLine(fmt.Sprintf("⚠️  Could not create ports.conf: %v", err))
		return
	}
	ps.logLine("✅ Created default ports.conf — edit it to customise ports, then restart.")
}

// ─── Cache cleaning ──────────────────────────────────────────────────────

// cleanCacheDir removes all entries from the cache directory except .env.
// This prevents stale files from a previous launcher version from persisting
// and being COPY'd into the Docker image.
func cleanCacheDir(cacheDir string) error {
	entries, err := os.ReadDir(cacheDir)
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		return err
	}

	for _, entry := range entries {
		if entry.Name() == ".env" || entry.Name() == "ports.conf" {
			continue
		}
		path := filepath.Join(cacheDir, entry.Name())
		if err := os.RemoveAll(path); err != nil {
			return fmt.Errorf("removing %s: %w", path, err)
		}
	}

	return nil
}

// ─── Base-image hash (avoids expensive rebuild when deps haven't changed) ──

// baseHashFiles lists the embedded files whose contents determine whether
// the base Docker image needs rebuilding.  App-code changes should NOT
// trigger a base rebuild; only changes to these specific files matter.
var baseHashFiles = []string{
	"embed/docker/Dockerfile.base",
	"embed/requirements.txt",
}

// baseHashPath returns the path where the base-image content hash is stored.
// Stored outside launcher-cache so it survives cache cleaning.
func baseHashPath(cacheDir string) string {
	return filepath.Join(filepath.Dir(cacheDir), "base-hash")
}

// computeBaseHash reads the embedded base-image dependencies and returns a
// SHA-256 hex digest.  If any of those files change between launcher
// versions, the hash will differ and the base image will be rebuilt.
func computeBaseHash() (string, error) {
	h := sha256.New()

	for _, f := range baseHashFiles {
		data, err := projectFS.ReadFile(f)
		if err != nil {
			return "", fmt.Errorf("read %s for hashing: %w", f, err)
		}
		h.Write(data)
	}

	return hex.EncodeToString(h.Sum(nil)), nil
}

// readBaseHash returns the previously stored base-image hash, or an empty
// string if no hash has been stored yet.
func readBaseHash(cacheDir string) (string, error) {
	data, err := os.ReadFile(baseHashPath(cacheDir))
	if err != nil {
		if os.IsNotExist(err) {
			return "", nil
		}
		return "", err
	}
	return strings.TrimSpace(string(data)), nil
}

// storeBaseHash writes a computed hash to disk so the next run can compare.
func storeBaseHash(cacheDir string, hash string) error {
	return os.WriteFile(baseHashPath(cacheDir), []byte(hash+"\n"), 0644)
}

// ─── Storage base ──────────────────────────────────────────────────────────

func getStorageBase() string {
	home := os.Getenv("HOME")
	if runtime.GOOS == "windows" {
		home = os.Getenv("USERPROFILE")
	}

	if home == "" {
		return filepath.Join(os.TempDir(), "AuroraCoder")
	}

	documents := filepath.Join(home, "Documents")
	if _, err := os.Stat(documents); err == nil {
		return filepath.Join(documents, "AuroraCoder")
	}

	return filepath.Join(home, "AuroraCoder")
}
