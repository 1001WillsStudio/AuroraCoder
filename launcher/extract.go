package main

import (
	"crypto/sha256"
	"embed"
	"encoding/hex"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"runtime"
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
	appPort       = 3000
	toolStorePort = 8765
	vncPort       = 6080
	apiPort       = 8080
	devPortStart  = 8900
	devPortEnd    = 8902
)

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
		if entry.Name() == ".env" {
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
