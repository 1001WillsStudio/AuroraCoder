package main

import (
	"embed"
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
	baseImageName = "thinkwithtool-base"
	appImageName  = "thinkwithtool"
	containerName = "thinkwithtool-agent"
	appPort       = 8081
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
		base = filepath.Join(os.Getenv("HOME"), "Library", "Caches", "ThinkWithTool")
	case "windows":
		base = filepath.Join(os.Getenv("APPDATA"), "ThinkWithTool")
	default:
		cacheHome := os.Getenv("XDG_CACHE_HOME")
		if cacheHome == "" {
			cacheHome = filepath.Join(os.Getenv("HOME"), ".cache")
		}
		base = filepath.Join(cacheHome, "thinkwithtool")
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
	envExamplePath := filepath.Join(cacheDir, ".env.example")

	if _, err := os.Stat(envPath); err == nil {
		ps.logLine("✅ .env file found.")
		return false
	}

	src, err := os.Open(envExamplePath)
	if err != nil {
		ps.logLine(fmt.Sprintf("⚠️  Could not open .env.example: %v", err))
		return true
	}
	defer src.Close()

	dst, err := os.Create(envPath)
	if err != nil {
		ps.logLine(fmt.Sprintf("⚠️  Could not create .env: %v", err))
		return true
	}
	defer dst.Close()

	io.Copy(dst, src)
	ps.logLine("⚠️  .env file created from template — please add your API keys.")
	return true
}

// ─── Storage base ──────────────────────────────────────────────────────────

func getStorageBase() string {
	home := os.Getenv("HOME")
	if runtime.GOOS == "windows" {
		home = os.Getenv("USERPROFILE")
	}

	if home == "" {
		return filepath.Join(os.TempDir(), "ThinkTool")
	}

	documents := filepath.Join(home, "Documents")
	if _, err := os.Stat(documents); err == nil {
		return filepath.Join(documents, "ThinkTool")
	}

	return filepath.Join(home, "ThinkTool")
}
