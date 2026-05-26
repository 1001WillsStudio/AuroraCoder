package main

import (
	"fmt"
	"net/http"
	"os"
	"runtime"
	"time"
)

// version is set at build time via -ldflags
var version = "dev"

func main() {
	printBanner()

	// Start the progress web server early so Docker-detection errors
	// show in the browser instead of a terminal-only message.
	ps := newProgressServer()
	go ps.listen()

	// Determine cache dir early — needed for .env path display in progress UI
	cacheDir, err := ensureCacheDir()
	if err != nil {
		fmt.Fprintf(os.Stderr, "❌ ERROR: %v\n", err)
		os.Exit(1)
	}

	// Open progress page in browser before Docker checks
	openBrowser(ps.url())
	fmt.Printf("\n  Progress page: %s\n\n", ps.url())

	// Check Docker — guide is now shown in the browser, not just terminal
	ds := detectDocker()

	if !ds.Installed {
		fmt.Println()
		fmt.Println("❌  Docker is not installed on this system.")
		ps.fail(dockerInstallGuideMessage())
		autoExit(1)
	}

	if !ds.Running {
		fmt.Println()
		fmt.Println("❌  Docker is installed but not running.")
		msg := "Docker is installed but not running.\n\n  Please start Docker and try again:\n"
		switch goos() {
		case "windows":
			msg += "    → Search for 'Docker Desktop' in Start Menu and launch it"
		case "darwin":
			msg += "    → Open Docker Desktop from /Applications"
		default:
			msg += "    → Run: sudo systemctl start docker"
		}
		ps.fail(msg)
		autoExit(1)
	}

	fmt.Println("  ✅ Docker is running.")


	// ── Run deployment steps ──────────────────────────────────────

	// Step 1: Extract
	ps.setStep(1, "running")
	ps.logLine("Extracting project files...")
	if err := extractProject(cacheDir); err != nil {
		ps.fail(fmt.Sprintf("Failed to extract project: %v", err))
		autoExit(1)
	}
	ps.logLine(fmt.Sprintf("Project extracted to: %s", cacheDir))
	ps.setStep(1, "done")

	// Step 2: .env file
	ps.setStep(2, "running")
	ps.logLine("Checking environment configuration...")
	needsEnvEdit := ensureEnvFile(cacheDir, ps)
	if needsEnvEdit {
		ps.warnStep(2, fmt.Sprintf(
			"Please edit your .env file with API keys:\n  %s\nThen restart the app.",
			cacheDir,
		))
	}
	ps.setStep(2, "done")

	// Step 3: Build base Docker image
	ps.setStep(3, "running")
	if err := buildBaseImage(cacheDir, ps); err != nil {
		ps.fail(fmt.Sprintf("Base image build failed: %v", err))
		autoExit(1)
	}
	ps.setStep(3, "done")

	// Step 4: Build app Docker image
	ps.setStep(4, "running")
	if err := buildAppImage(cacheDir, ps); err != nil {
		ps.fail(fmt.Sprintf("App image build failed: %v", err))
		autoExit(1)
	}
	ps.setStep(4, "done")

	// Step 5: Start container
	ps.setStep(5, "running")
	ps.logLine("Starting container...")
	if err := startContainer(cacheDir, ps); err != nil {
		ps.fail(fmt.Sprintf("Container start failed: %v", err))
		autoExit(1)
	}
	ps.logLine("Container started, waiting for services to be ready...")
	appURL := fmt.Sprintf("http://localhost:%d", appPort)
	if err := waitForApp(appURL, ps, 60*time.Second); err != nil {
		ps.logLine(fmt.Sprintf("⚠️  Health check warning: %v", err))
		ps.logLine("The app may still be starting — please refresh if needed.")
	}
	ps.setStep(5, "done")

	// ── Done ──────────────────────────────────────────────────────
	ps.done(appURL)

	fmt.Println()
	fmt.Println("════════════════════════════════════════")
	fmt.Println("  AuroraCoder is running!")
	fmt.Printf("  →  http://localhost:%d\n", appPort)
	fmt.Println("════════════════════════════════════════")
	fmt.Println()
	fmt.Println("  API Docs:      http://localhost:8080/docs")
	fmt.Println("  VNC Desktop:   http://localhost:6080")
	fmt.Printf("  ToolStore:     http://localhost:%d\n", toolStorePort)
	fmt.Println()
	fmt.Println("  To stop:  docker stop thinkwithtool-agent")
	fmt.Println()

	autoExit(0)
}

func printBanner() {
	fmt.Println()
	fmt.Println("╔══════════════════════════════════════════════╗")
	fmt.Println("║          AuroraCoder Launcher                ║")
	if version != "dev" {
		fmt.Printf("║          v%s                              ║\n", version)
	}
	fmt.Println("║     One-Click Docker Deployment              ║")
	fmt.Println("╚══════════════════════════════════════════════╝")
}

// waitForApp polls the app URL until it responds with a non-5xx status,
// or the timeout expires.  Services inside the container are not
// guaranteed to be ready immediately after `docker run` returns,
// so this gives them a grace period before the browser redirects.
func waitForApp(url string, ps *progressServer, timeout time.Duration) error {
	deadline := time.Now().Add(timeout)
	client := &http.Client{Timeout: 3 * time.Second}
	consecutive := 0

	for {
		if time.Now().After(deadline) {
			return fmt.Errorf("timed out after %v", timeout)
		}

		resp, err := client.Get(url)
		if err == nil {
			resp.Body.Close()
			if resp.StatusCode < 500 {
				consecutive++
				// Require 2 consecutive successful responses
				// to avoid a false-positive during a restart
				if consecutive >= 2 {
					return nil
				}
			} else {
				consecutive = 0
			}
		} else {
			consecutive = 0
		}

		time.Sleep(2 * time.Second)
	}
}

// autoExit waits briefly so the browser can receive the final SSE event,
// then terminates the process without requiring user input.
func autoExit(code int) {
	if code != 0 {
		fmt.Printf("\nExiting in 3 seconds... (error code %d)\n", code)
	} else {
		fmt.Println("\nLauncher finished — exiting in 3 seconds...")
	}
	time.Sleep(3 * time.Second)
	os.Exit(code)
}

// dockerInstallGuideMessage returns a platform-specific Docker installation
// guide as plain text.  It is shown in the browser via ps.fail(), replacing
// the old terminal-only printDockerInstallGuide().
func dockerInstallGuideMessage() string {
	switch runtime.GOOS {
	case "windows":
		return `Docker is not installed on this system.

═══ How to install Docker on Windows ═══

  1. Download Docker Desktop for Windows:
     https://www.docker.com/products/docker-desktop/

  2. Run the installer (Docker Desktop Installer.exe)

  3. After installation, Docker Desktop starts automatically.
     Look for the whale icon in the system tray.

  4. If prompted, install WSL 2 (Windows Subsystem for Linux).
     Docker Desktop will guide you through it.

  5. Once the whale icon stops animating, Docker is ready.
     Then run this launcher again.`

	case "darwin":
		arch := runtime.GOARCH
		url := "https://desktop.docker.com/mac/main/amd64/Docker.dmg"
		if arch == "arm64" {
			url = "https://desktop.docker.com/mac/main/arm64/Docker.dmg"
		}
		return fmt.Sprintf(`Docker is not installed on this system.

═══ How to install Docker on macOS ═══

  Option A — Homebrew (recommended):
    brew install --cask docker

  Option B — Direct download:
    %s
    Open the .dmg and drag Docker to /Applications
    Launch Docker from /Applications

  Once installed, run this launcher again.`, url)

	default:
		return `Docker is not installed on this system.

═══ How to install Docker on Linux ═══

  Use the official convenience script:

    curl -fsSL https://get.docker.com | sudo sh

  Then add your user to the docker group:

    sudo usermod -aG docker $USER

  Log out and back in, or run:

    newgrp docker

  Then run this launcher again.

  ── Distro-specific packages ──

  Ubuntu / Debian:
    sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2

  Fedora:
    sudo dnf install -y docker docker-compose
    sudo systemctl enable --now docker

  Arch:
    sudo pacman -S docker docker-compose
    sudo systemctl enable --now docker`
	}
}
