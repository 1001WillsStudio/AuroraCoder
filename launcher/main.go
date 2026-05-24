package main

import (
	"fmt"
	"os"
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
	ps.logLine("Container started, waiting for services...")
	ps.setStep(5, "done")

	// ── Done ──────────────────────────────────────────────────────
	ps.done(fmt.Sprintf("http://localhost:%d", appPort))

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

func pressEnterToExit(code int) {
	fmt.Println()
	fmt.Print("Press Enter to exit...")
	fmt.Scanln()
	os.Exit(code)
}
