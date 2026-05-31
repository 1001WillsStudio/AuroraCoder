package main

import (
	"bufio"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
)

// ─── Docker detection ─────────────────────────────────────────────────────

type DockerStatus struct {
	Installed bool
	Running   bool
}

func detectDocker() DockerStatus {
	ds := DockerStatus{}

	// Check if docker binary exists
	if _, err := exec.LookPath("docker"); err != nil {
		return ds // neither installed nor running
	}
	ds.Installed = true

	// Check if the daemon is responsive
	cmd := exec.Command("docker", "info")
	cmd.Stdout = nil
	cmd.Stderr = nil
	if err := cmd.Run(); err == nil {
		ds.Running = true
	}

	return ds
}

// ─── Chinese mirror fallback for Docker Hub pull failures ─────────────

// chinaPullSources lists Chinese registries that serve as Docker Hub
// pull-through caches and accept direct "docker pull" requests.
// When a docker build fails (often due to Docker Hub being slow or
// unreachable from mainland China), the launcher tries pulling the
// base image from these mirrors, tags it locally, then retries.
// Docker's system configuration (daemon.json) is NEVER modified.
var chinaPullSources = []string{
	"docker.m.daocloud.io",
	"hub-mirror.c.163.com",
}

// tryChinaMirrorPull attempts to pull a Docker Hub image from Chinese
// mirror registries. On success the image is tagged with the original
// Docker Hub name so subsequent docker build steps find it cached.
// Returns true if at least one mirror succeeded.
func tryChinaMirrorPull(dockerHubImage string, ps *progressServer) bool {
	for _, mirror := range chinaPullSources {
		mirrorImage := mirror + "/" + dockerHubImage
		ps.logLine(fmt.Sprintf("   Trying mirror pull: %s", mirrorImage))

		// Pull from mirror — this is a regular docker pull, streamed to UI
		cmd := exec.Command("docker", "pull", mirrorImage)
		if err := streamCommand(cmd, ps); err != nil {
			ps.logLine(fmt.Sprintf("   ✗ Mirror %s: %v", mirror, err))
			continue
		}

		// Tag it so the original FROM line in the Dockerfile resolves locally
		tagCmd := exec.Command("docker", "tag", mirrorImage, dockerHubImage)
		if out, err := tagCmd.CombinedOutput(); err != nil {
			ps.logLine(fmt.Sprintf("   ✗ Tag failed: %v — %s", err, strings.TrimSpace(string(out))))
			continue
		}

		ps.logLine(fmt.Sprintf("   ✅ Pulled %s via %s and tagged for local use.", dockerHubImage, mirror))
		return true
	}
	return false
}

// ─── Docker installation guide ────────────────────────────────────────────

func printDockerInstallGuide() {
	switch runtime.GOOS {
	case "windows":
		fmt.Println("═══ How to install Docker on Windows ═══")
		fmt.Println()
		fmt.Println("  1. Download Docker Desktop for Windows:")
		fmt.Println("     https://www.docker.com/products/docker-desktop/")
		fmt.Println()
		fmt.Println("  2. Run the installer (Docker Desktop Installer.exe)")
		fmt.Println()
		fmt.Println("  3. After installation, Docker Desktop starts automatically.")
		fmt.Println("     Look for the whale icon in the system tray.")
		fmt.Println()
		fmt.Println("  4. If prompted, install WSL 2 (Windows Subsystem for Linux).")
		fmt.Println("     Docker Desktop will guide you through it.")
		fmt.Println()
		fmt.Println("  5. Once the whale icon stops animating, Docker is ready.")
		fmt.Println("     Then run this launcher again.")

	case "darwin":
		fmt.Println("═══ How to install Docker on macOS ═══")
		fmt.Println()
		fmt.Println("  Option A — Homebrew (recommended):")
		fmt.Println("    brew install --cask docker")
		fmt.Println()
		fmt.Println("  Option B — Direct download:")
		fmt.Println("    1. Download Docker Desktop for Mac:")
		arch := runtime.GOARCH
		if arch == "arm64" {
			fmt.Println("       https://desktop.docker.com/mac/main/arm64/Docker.dmg")
		} else {
			fmt.Println("       https://desktop.docker.com/mac/main/amd64/Docker.dmg")
		}
		fmt.Println("    2. Open the .dmg and drag Docker to /Applications")
		fmt.Println("    3. Launch Docker from /Applications")
		fmt.Println()
		fmt.Println("  Once installed, run this launcher again.")

	default: // linux
		fmt.Println("═══ How to install Docker on Linux ═══")
		fmt.Println()
		fmt.Println("  Use the official convenience script:")
		fmt.Println()
		fmt.Println("    curl -fsSL https://get.docker.com | sudo sh")
		fmt.Println()
		fmt.Println("  Then add your user to the docker group:")
		fmt.Println()
		fmt.Println("    sudo usermod -aG docker $USER")
		fmt.Println()
		fmt.Println("  Log out and back in, or run:")
		fmt.Println()
		fmt.Println("    newgrp docker")
		fmt.Println()
		fmt.Println("  Then run this launcher again.")
		fmt.Println()
		fmt.Println("  ── Distro-specific packages ──")
		fmt.Println()
		fmt.Println("  Ubuntu / Debian:")
		fmt.Println("    sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2")
		fmt.Println()
		fmt.Println("  Fedora:")
		fmt.Println("    sudo dnf install -y docker docker-compose")
		fmt.Println("    sudo systemctl enable --now docker")
		fmt.Println()
		fmt.Println("  Arch:")
		fmt.Println("    sudo pacman -S docker docker-compose")
		fmt.Println("    sudo systemctl enable --now docker")
	}
	fmt.Println()
}

// ─── Base image build ─────────────────────────────────────────────────────

// baseImageDockerHub is the FROM image used in Dockerfile.base.
// On build failure we try pulling it from Chinese mirrors and retrying.
const baseImageDockerHub = "continuumio/miniconda3:latest"

func buildBaseImage(cacheDir string, ps *progressServer) error {
	// ── Hash-based cache check ──
	// Only skip the base image build when both (a) the image exists AND
	// (b) its content hash matches the current embedded dependencies.
	// This prevents expensive rebuilds when only app code changes,
	// while correctly forcing a rebuild when Dockerfile.base or
	// requirements.txt have been modified in a new launcher version.

	currentHash, hashErr := computeBaseHash()
	if hashErr != nil {
		ps.logLine(fmt.Sprintf("⚠️  Cannot compute base hash: %v", hashErr))
		// Fall through to build — safer than skipping on hash failure
	} else {
		storedHash, _ := readBaseHash(cacheDir)
		inspectCmd := exec.Command("docker", "inspect", "--type=image", baseImageName)
		if inspectCmd.Run() == nil && storedHash == currentHash && storedHash != "" {
			ps.logLine("✅ Base image up to date (dependencies unchanged), skipping build.")
			return nil
		}
	}

	ps.logLine("Building auroracoder-base (may take several minutes)...")

	cmd := exec.Command("docker", "build", "-t", baseImageName, "-f", "docker/Dockerfile.base", ".")
	cmd.Dir = cacheDir

	err := streamCommand(cmd, ps)
	if err != nil {
		// Docker Hub might be slow or unreachable (common in mainland China).
		// Try pulling the base image from a Chinese mirror, tag it locally,
		// then retry the build. Docker daemon config is never touched.
		ps.logLine("")
		ps.logLine("⚠️  Build failed — may be a Docker Hub network issue.")
		ps.logLine("   Attempting fallback: pull base image from Chinese mirrors...")

		if tryChinaMirrorPull(baseImageDockerHub, ps) {
			ps.logLine("   Retrying docker build with locally-cached base image...")
			cmd2 := exec.Command("docker", "build", "-t", baseImageName, "-f", "docker/Dockerfile.base", ".")
			cmd2.Dir = cacheDir
			return streamCommand(cmd2, ps)
		}

		// Mirror pull also failed — give the user actionable guidance
		ps.logLine("")
		ps.logLine("   All mirrors exhausted. Manual fix options:")
		ps.logLine("   1. Configure a Docker registry mirror in Docker Desktop / daemon.json:")
		ps.logLine("      { \"registry-mirrors\": [\"https://registry.cn-hangzhou.aliyuncs.com\"] }")
		ps.logLine("   2. Or use a VPN/proxy to access Docker Hub directly.")
		ps.logLine("   3. Then re-run the launcher.")
	} else if currentHash != "" {
		// Store the hash only after a successful build
		if storeErr := storeBaseHash(cacheDir, currentHash); storeErr != nil {
			ps.logLine(fmt.Sprintf("⚠️  Could not store base hash: %v", storeErr))
		}
	}
	return err
}

// ─── App image build ──────────────────────────────────────────────────────

func buildAppImage(cacheDir string, ps *progressServer) error {
	ps.logLine("Building auroracoder app image...")

	cmd := exec.Command("docker", "build", "-t", appImageName, "-f", "docker/Dockerfile", ".")
	cmd.Dir = cacheDir

	return streamCommand(cmd, ps)
}

// ─── Container start ──────────────────────────────────────────────────────

func startContainer(cacheDir string, ps *progressServer) error {
	// Stop and remove any existing container
	stopCmd := exec.Command("docker", "stop", containerName)
	stopCmd.Run()
	rmCmd := exec.Command("docker", "rm", containerName)
	rmCmd.Run()

	storageBase := getStorageBase()
	dataDir := filepath.Join(storageBase, "data")
	workspaceDir := filepath.Join(storageBase, "workspace")
	os.MkdirAll(dataDir, 0755)
	os.MkdirAll(workspaceDir, 0755)

	envFile := filepath.Join(cacheDir, ".env")

	args := []string{
		"run", "--rm", "-d",
		"--name", containerName,
		"--env-file", envFile,
		"-e", "AURORACODER_DOCKER=1",
		"-e", "AURORACODER_VNC=1",
		"-v", fmt.Sprintf("%s:/app/data", dataDir),
		"-v", fmt.Sprintf("%s:/workspace", workspaceDir),
		"-p", fmt.Sprintf("%d:%d", apiPort, apiPort),
		"-p", fmt.Sprintf("%d:%d", appPort, appPort),
		"-p", fmt.Sprintf("%d:%d", vncPort, vncPort),
		"-p", fmt.Sprintf("%d:%d", toolStorePort, toolStorePort),
		"-p", fmt.Sprintf("%d-%d:%d-%d", devPortStart, devPortEnd, devPortStart, devPortEnd),
		appImageName,
	}

	cmd := exec.Command("docker", args...)
	cmd.Dir = cacheDir

	return streamCommand(cmd, ps)
}

// ─── Stream command output ────────────────────────────────────────────────

func streamCommand(cmd *exec.Cmd, ps *progressServer) error {
	// Combine stdout and stderr
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return fmt.Errorf("stdout pipe: %w", err)
	}
	cmd.Stderr = cmd.Stdout // redirect stderr to same pipe

	if err := cmd.Start(); err != nil {
		return fmt.Errorf("start command: %w", err)
	}

	// Stream output line by line to the progress server
	scanner := bufio.NewScanner(stdout)
	// Docker build can produce long lines, bump the buffer
	scanner.Buffer(make([]byte, 64*1024), 256*1024)

	for scanner.Scan() {
		line := scanner.Text()
		ps.logLine(line)
		// Also print to terminal for terminal users
		fmt.Println("  " + line)
	}

	// Wait for command to finish
	waitErr := cmd.Wait()

	// Check for scan errors before returning wait result
	if scanErr := scanner.Err(); scanErr != nil && scanErr != io.EOF {
		ps.logLine(fmt.Sprintf("⚠️  Output stream error: %v", scanErr))
	}

	if waitErr != nil {
		return fmt.Errorf("command failed: %w", waitErr)
	}

	return nil
}

// ─── Browser open ─────────────────────────────────────────────────────────

func openBrowser(url string) {
	var cmd *exec.Cmd

	switch runtime.GOOS {
	case "darwin":
		cmd = exec.Command("open", url)
	case "windows":
		cmd = exec.Command("rundll32", "url.dll,FileProtocolHandler", url)
	default:
		if _, err := exec.LookPath("xdg-open"); err == nil {
			cmd = exec.Command("xdg-open", url)
		} else if _, err := exec.LookPath("sensible-browser"); err == nil {
			cmd = exec.Command("sensible-browser", url)
		} else {
			fmt.Printf("  Please open %s in your browser.\n", url)
			return
		}
	}

	if err := cmd.Start(); err != nil {
		fmt.Printf("  Could not open browser automatically: %v\n", err)
		fmt.Printf("  Please open %s in your browser.\n", url)
		return
	}
}

// ─── Platform info helper ─────────────────────────────────────────────────

func platformInfo() string {
	return fmt.Sprintf("%s/%s", runtime.GOOS, runtime.GOARCH)
}

// detectDistro attempts to identify the Linux distribution (for install guide context).
// Not used directly — the install guide covers all major distros statically.
func detectDistro() string {
	if runtime.GOOS != "linux" {
		return ""
	}
	data, err := os.ReadFile("/etc/os-release")
	if err != nil {
		return "linux"
	}
	for _, line := range strings.Split(string(data), "\n") {
		if strings.HasPrefix(line, "ID=") {
			return strings.Trim(strings.TrimPrefix(line, "ID="), "\"")
		}
	}
	return "linux"
}
