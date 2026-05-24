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

func buildBaseImage(cacheDir string, ps *progressServer) error {
	// Check if base image already exists
	cmd := exec.Command("docker", "inspect", "--type=image", baseImageName)
	if err := cmd.Run(); err == nil {
		ps.logLine("✅ Base image already cached, skipping build.")
		return nil
	}

	ps.logLine("Building thinkwithtool-base (first time may take several minutes)...")

	cmd = exec.Command("docker", "build", "-t", baseImageName, "-f", "docker/Dockerfile.base", ".")
	cmd.Dir = cacheDir

	return streamCommand(cmd, ps)
}

// ─── App image build ──────────────────────────────────────────────────────

func buildAppImage(cacheDir string, ps *progressServer) error {
	ps.logLine("Building thinkwithtool app image...")

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
		"-e", "THINKTOOL_DOCKER=1",
		"-e", "THINKTOOL_VNC=1",
		"-v", fmt.Sprintf("%s:/app/data", dataDir),
		"-v", fmt.Sprintf("%s:/workspace", workspaceDir),
		"-p", fmt.Sprintf("%d:%d", apiPort, apiPort),
		"-p", fmt.Sprintf("%d:%d", appPort, appPort),
		"-p", fmt.Sprintf("%d:%d", vncPort, vncPort),
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
