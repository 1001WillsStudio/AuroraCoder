package main

import (
	"fmt"
	"net/http"
	"os"
	"time"
)

// version is set at build time via -ldflags
var version = "dev"

// gpuMode is set at build time via ldflags: "-X main.gpuMode=true"
// When true, the launcher builds GPU images and uses --gpus all.
var gpuMode = "false"

func isGpu() bool { return gpuMode == "true" }

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

	// ── Step 1: Check Docker ───────────────────────────────────────

	ps.setStep(1, "running")
	ps.logLine("Checking Docker installation...")

	ds := detectDocker()

	if !ds.Installed {
		ps.setStepMsg(1, "warning", "Docker is not installed")
		fmt.Println()
		fmt.Println("❌  Docker is not installed on this system.")
		openBrowser("https://www.docker.com/products/docker-desktop/")
		ps.instruction(dockerInstallGuideMessage())
		autoExit(1)
	}

	if !ds.Running {
		ps.setStepMsg(1, "warning", "Docker installed but not running")
		fmt.Println()
		fmt.Println("❌  Docker is installed but not running.")
		msg := "Docker is installed but not running.\n\nPlease start Docker and try again:\n"
		switch goos() {
		case "windows":
			msg += "  → Search for 'Docker Desktop' in Start Menu and launch it"
		case "darwin":
			msg += "  → Open Docker Desktop from /Applications"
		default:
			msg += "  → Run: sudo systemctl start docker"
		}
		ps.instruction(msg)
		autoExit(1)
	}

	ps.setStepMsg(1, "done", "Docker is installed and running")
	ps.logLine("✅ Docker is running.")

	// ── Stop old container NOW ──────────────────────────────────
	// Docker builds (steps 4-5) are slow. By stopping the old
	// container here, ports have the entire build duration to be
	// released before we need to bind them in step 6.
	stopOldContainer(ps)

	// ── Step 2: Extract ───────────────────────────────────────────

	ps.setStep(2, "running")
	ps.logLine("Cleaning stale files from previous version...")
	cleanCacheDir(cacheDir)
	ps.logLine("Extracting project files...")
	if err := extractProject(cacheDir); err != nil {
		ps.fail(fmt.Sprintf("Failed to extract project: %v", err))
		autoExit(1)
	}
	ps.logLine(fmt.Sprintf("Project extracted to: %s", cacheDir))
	ps.setStep(2, "done")

	// Step 3: .env file
	ps.setStep(3, "running")
	ps.logLine("Checking environment configuration...")
	needsEnvEdit := ensureEnvFile(cacheDir, ps)
	if needsEnvEdit {
		ps.warnStep(3, fmt.Sprintf(
			"Please edit your .env file with API keys:\n  %s\nThen restart the app.",
			cacheDir,
		))
	}
	ps.setStep(3, "done")

	// Step 3b: Ensure ports.conf exists
	ensurePortsConf(cacheDir, ps)

	// Step 4: Build base Docker image
 	ps.setStep(4, "running")
 	if err := buildBaseImage(cacheDir, ps); err != nil {
 		ps.fail(fmt.Sprintf("Base image build failed: %v", err))
 		autoExit(1)
 	}
 	ps.setStep(4, "done")

	var appURL string
	var ports PortsConfig

	if isGpu() {
		// GPU Step 5: Build GPU base Docker image
		ps.setStep(5, "running")
		if err := buildGpuBaseImage(cacheDir, ps); err != nil {
			ps.fail(fmt.Sprintf("GPU base image build failed: %v", err))
			autoExit(1)
		}
		ps.setStep(5, "done")

		// GPU Step 6: Build GPU app Docker image
		ps.setStep(6, "running")
		if err := buildGpuAppImage(cacheDir, ps); err != nil {
			ps.fail(fmt.Sprintf("GPU app image build failed: %v", err))
			autoExit(1)
		}
		ps.setStep(6, "done")

		// GPU Step 7: Start GPU container
		ps.setStep(7, "running")
		ps.logLine("Starting GPU container (--gpus all)...")
		ports, err = startGpuContainer(cacheDir, ps)
		if err != nil {
			ps.fail(fmt.Sprintf("GPU container start failed: %v", err))
			autoExit(1)
		}
		ps.logLine("GPU container started, waiting for services to be ready...")
		appURL = fmt.Sprintf("http://localhost:%d", ports.Frontend)
		if err := waitForApp(appURL, ps, 60*time.Second); err != nil {
			ps.logLine(fmt.Sprintf("⚠️  Health check warning: %v", err))
			ps.logLine("The app may still be starting — please refresh if needed.")
		}
		ps.setStep(7, "done")
	} else {
		// Step 5: Build app Docker image
		ps.setStep(5, "running")
		if err := buildAppImage(cacheDir, ps); err != nil {
			ps.fail(fmt.Sprintf("App image build failed: %v", err))
			autoExit(1)
		}
		ps.setStep(5, "done")

		// Step 6: Start container
		ps.setStep(6, "running")
		ps.logLine("Starting container...")
 		ports, err = startContainer(cacheDir, ps)
		if err != nil {
			ps.fail(fmt.Sprintf("Container start failed: %v", err))
			autoExit(1)
		}
		ps.logLine("Container started, waiting for services to be ready...")
 		appURL = fmt.Sprintf("http://localhost:%d", ports.Frontend)
		if err := waitForApp(appURL, ps, 60*time.Second); err != nil {
			ps.logLine(fmt.Sprintf("⚠️  Health check warning: %v", err))
			ps.logLine("The app may still be starting — please refresh if needed.")
		}
		ps.setStep(6, "done")
	}

 	// ── Done ──────────────────────────────────────────────────────
 	ps.done(appURL)

 	fmt.Println()
 	fmt.Println("════════════════════════════════════════")
 	if isGpu() {
 		fmt.Println("  AuroraCoder GPU is running!")
 	} else {
 		fmt.Println("  AuroraCoder is running!")
 	}
 	fmt.Printf("  →  http://localhost:%d\n", ports.Frontend)
 	fmt.Println("════════════════════════════════════════")
 	fmt.Println()
 	fmt.Printf("  API Docs:      http://localhost:%d/docs\n", ports.Backend)
 	fmt.Printf("  VNC Desktop:   http://localhost:%d\n", ports.VNC)
 	fmt.Printf("  ToolStore:     http://localhost:%d\n", ports.ToolStore)
 	fmt.Println()
 	if isGpu() {
 		fmt.Println("  To stop:  docker stop auroracoder-agent-gpu")
 	} else {
 		fmt.Println("  To stop:  docker stop auroracoder-agent")
 	}
	fmt.Println()

	autoExit(0)
}

func printBanner() {
	fmt.Println()
	fmt.Println("╔══════════════════════════════════════════════╗")
	label := "AuroraCoder Launcher"
	if isGpu() {
		label = "AuroraCoder GPU Launcher"
	}
	fmt.Printf("║          %-36s ║\n", label)
	if version != "dev" {
		fmt.Printf("║          v%-36s ║\n", version)
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

// dockerInstallGuideMessage returns a short message pointing the user to
// the Docker download page.  It fits cleanly inside the progress-page
// error box and opens the download URL in their browser.
func dockerInstallGuideMessage() string {
	return "Docker is not installed on this system.\n\n" +
		"👉  https://www.docker.com/products/docker-desktop/\n\n" +
		"Download and install Docker, then run this launcher again."
}
