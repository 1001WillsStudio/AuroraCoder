package main

import (
	"fmt"
	"net/http"
	"os"
	"time"
)

// version is set at build time via -ldflags
var version = "dev"

func main() {
	printBanner()

	// Check Docker — provide OS-specific install guide if missing
	ds := detectDocker()

	if !ds.Installed {
		fmt.Println()
		fmt.Println("❌  Docker is not installed on this system.")
		fmt.Println()
		printDockerInstallGuide()
		pressEnterToExit(1)
	}

	if !ds.Running {
		fmt.Println()
		fmt.Println("❌  Docker is installed but not running.")
		fmt.Println()
		fmt.Println("  Please start Docker Desktop and try again:")
		switch goos() {
		case "windows":
			fmt.Println("    → Search for 'Docker Desktop' in Start Menu and launch it")
		case "darwin":
			fmt.Println("    → Open Docker Desktop from /Applications")
		default:
			fmt.Println("    → Run: sudo systemctl start docker")
		}
		pressEnterToExit(1)
	}

	fmt.Println("  ✅ Docker is running.")

	// Start the progress web server
	ps := newProgressServer()
	go ps.listen()

	// Determine cache dir early — needed for .env path display in progress UI
	cacheDir, err := ensureCacheDir()
	if err != nil {
		fmt.Fprintf(os.Stderr, "❌ ERROR: %v\n", err)
		pressEnterToExit(1)
	}

	// Open progress page in browser
	openBrowser(ps.url())
	fmt.Printf("\n  Progress page: %s\n\n", ps.url())


	// ── Run deployment steps ──────────────────────────────────────

	// Step 1: Extract
	ps.setStep(1, "running")
	ps.logLine("Extracting project files...")
	if err := extractProject(cacheDir); err != nil {
		ps.fail(fmt.Sprintf("Failed to extract project: %v", err))
		pressEnterToExit(1)
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
		pressEnterToExit(1)
	}
	ps.setStep(3, "done")

	// Step 4: Build app Docker image
	ps.setStep(4, "running")
	if err := buildAppImage(cacheDir, ps); err != nil {
		ps.fail(fmt.Sprintf("App image build failed: %v", err))
		pressEnterToExit(1)
	}
	ps.setStep(4, "done")

	// Step 5: Start container
	ps.setStep(5, "running")
	ps.logLine("Starting container...")
	if err := startContainer(cacheDir, ps); err != nil {
		ps.fail(fmt.Sprintf("Container start failed: %v", err))
		pressEnterToExit(1)
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

	pressEnterToExit(0)
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

func pressEnterToExit(code int) {
	fmt.Println()
	fmt.Print("Press Enter to exit...")
	fmt.Scanln()
	os.Exit(code)
}
