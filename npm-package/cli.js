#!/usr/bin/env node

/**
 * AuroraCoder — Native npm Launcher
 *
 * Starts the AuroraCoder agent backend + gateway + frontend directly on the
 * host machine (no Docker required).  One command and you're in the browser.
 *
 * Usage:
 *   npx aurora-coder            # auto-finds project root
 *   npx aurora-coder --port 9000
 *   npx aurora-coder --help
 *
 * Prerequisites:
 *   - Node.js >= 18
 *   - Python >= 3.10  (python3 on PATH)
 *   - A DEEPSEEK_API_KEY in your environment or .env file
 *
 * Co-authored-by: AuroraCoderAgent <aurorathesnowyfox@gmail.com>
 */

"use strict";

const { spawn, execSync } = require("child_process");
const path = require("path");
const fs = require("fs");
const os = require("os");

// ---------------------------------------------------------------------------
// CLI argument parsing
// ---------------------------------------------------------------------------

const args = process.argv.slice(2);
let gatewayPort = 8081;
let backendPort = 8080;
let showHelp = false;

for (let i = 0; i < args.length; i++) {
  switch (args[i]) {
    case "--port":
    case "-p":
      gatewayPort = parseInt(args[++i], 10);
      if (isNaN(gatewayPort) || gatewayPort < 1) {
        console.error("❌ Invalid port number:", args[i]);
        process.exit(1);
      }
      break;
    case "--backend-port":
      backendPort = parseInt(args[++i], 10);
      if (isNaN(backendPort) || backendPort < 1) {
        console.error("❌ Invalid backend port:", args[i]);
        process.exit(1);
      }
      break;
    case "--help":
    case "-h":
      showHelp = true;
      break;
    default:
      console.error("❌ Unknown flag:", args[i]);
      console.error("   Try: npx aurora-coder --help");
      process.exit(1);
  }
}

if (showHelp) {
  console.log(`
  ✨ AuroraCoder — Native Launcher ✨

  Starts the AuroraCoder coding agent on your machine.

  USAGE:
    npx aurora-coder [flags]

  FLAGS:
    --port, -p <n>       Gateway/frontend port (default: 8081)
    --backend-port <n>   Agent backend port  (default: 8080)
    --help, -h           Show this message

  PREREQUISITES:
    Node.js >= 18  |  Python >= 3.10  |  DEEPSEEK_API_KEY set

  The launcher auto-discovers the project root (looks for src/, gateway/,
  frontend/).  On first run it installs Python deps and builds the frontend —
  subsequent starts are instant.
`);
  process.exit(0);
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const COLORS = { reset: "\x1b[0m", green: "\x1b[32m", yellow: "\x1b[33m", cyan: "\x1b[36m", red: "\x1b[31m", bold: "\x1b[1m" };
const c = (color, s) => `${COLORS[color]}${s}${COLORS.reset}`;
const log = (msg) => console.log(`  ${msg}`);
const step = (msg) => console.log(`\n${c("cyan", "▸")} ${c("bold", msg)}`);
const ok = (msg) => console.log(`  ${c("green", "✔")} ${msg}`);
const warn = (msg) => console.log(`  ${c("yellow", "⚠")} ${msg}`);

/** Run a command synchronously, returning stdout or throwing. */
function run(cmd, opts = {}) {
  const result = execSync(cmd, { encoding: "utf-8", stdio: ["ignore", "pipe", "pipe"], ...opts });
  return result != null ? result.trim() : "";
}

/** Spawn a long-running process and return it. */
function spawnProc(cmd, args, opts = {}) {
  const child = spawn(cmd, args, {
    stdio: "inherit",
    ...opts,
  });
  child.on("error", (err) => {
    console.error(`${c("red", "✖")} Failed to start ${cmd}: ${err.message}`);
  });
  return child;
}

/** Find a command on PATH; returns full path or null. */
function which(cmd) {
  try {
    return run(os.platform() === "win32" ? `where ${cmd}` : `which ${cmd}`, { stdio: "pipe" }).split("\n")[0].trim();
  } catch {
    return null;
  }
}

/** Open a URL in the default browser (cross-platform). */
function openBrowser(url) {
  const platform = os.platform();
  const cmd =
    platform === "darwin" ? "open" :
    platform === "win32" ? "start" : "xdg-open";
  try {
    const child = spawn(cmd, [url], { detached: true, stdio: "ignore" });
    // Suppress async errors (e.g. xdg-open not found on headless systems)
    child.on("error", () => {});
    child.unref();
    return true;
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// 1. Locate the AuroraCoder project root
// ---------------------------------------------------------------------------

step("Locating AuroraCoder project root…");

/**
 * Resolve the project root from the npm-package directory.
 *
 * When installed globally via npm, the CLI script lives inside the package's
 * own tree.  We look for the tell-tale directories: src/, gateway/, frontend/.
 *
 * Search order:
 *   1. Parent of this script's directory (npm-package/ -> Aurora Coder/)
 *   2. Working directory (if user runs from within the repo)
 *   3. AURORACODER_HOME environment variable
 */
function findProjectRoot() {
  // Env override
  if (process.env.AURORACODER_HOME) {
    const p = path.resolve(process.env.AURORACODER_HOME);
    if (fs.existsSync(path.join(p, "src")) && fs.existsSync(path.join(p, "gateway"))) return p;
  }

  const candidates = [
    path.resolve(__dirname, ".."),           // npm-package/ -> Aurora Coder/
    path.resolve(__dirname, "..", ".."),     // if nested deeper
    process.cwd(),
  ];

  for (const dir of candidates) {
    if (fs.existsSync(path.join(dir, "src")) && fs.existsSync(path.join(dir, "gateway")) && fs.existsSync(path.join(dir, "frontend"))) {
      return dir;
    }
  }
  return null;
}

const PROJECT_ROOT = findProjectRoot();
if (!PROJECT_ROOT) {
  console.error(`${c("red", "✖")} Could not find AuroraCoder project root.`);
  console.error("   Make sure the package is inside an AuroraCoder checkout, or set AURORACODER_HOME.");
  process.exit(1);
}
ok(`Found: ${PROJECT_ROOT}`);

// ---------------------------------------------------------------------------
// 2. Check prerequisites
// ---------------------------------------------------------------------------

step("Checking prerequisites…");

// -- Python --
const pythonCmd = which("python3") || which("python");
if (!pythonCmd) {
  console.error(`${c("red", "✖")} Python 3.10+ is required but not found on PATH.`);
  console.error("   Install from https://python.org or use your system package manager.");
  process.exit(1);
}
let pythonOk = false;
try {
  const ver = run(`${pythonCmd} --version`);
  const m = ver.match(/Python\s+(\d+)\.(\d+)/);
  if (m && (parseInt(m[1]) > 3 || (parseInt(m[1]) === 3 && parseInt(m[2]) >= 10))) {
    pythonOk = true;
    ok(`Python: ${ver}`);
  }
} catch { /* handled below */ }
if (!pythonOk) {
  warn(`Python version may be < 3.10.  AuroraCoder needs 3.10+.`);
}

// -- pip --
const pipCmd = which("pip3") || which("pip");
if (pipCmd) {
  ok(`pip: found`);
} else {
  warn("pip not found — will try python -m pip");
}

// -- Node --
try {
  const nv = run("node --version");
  ok(`Node: ${nv}`);
} catch {
  console.error(`${c("red", "✖")} Node.js is required.`);
  process.exit(1);
}

// -- API key warning --
if (!process.env.DEEPSEEK_API_KEY) {
  warn("DEEPSEEK_API_KEY is not set.  The agent will not work without it.");
  warn("Set it via: export DEEPSEEK_API_KEY=sk-...");
  warn("Or create a .env file in the project root.");
}

// ---------------------------------------------------------------------------
// 3. Install Python dependencies
// ---------------------------------------------------------------------------

step("Installing Python dependencies…");

const reqFile = path.join(PROJECT_ROOT, "requirements.txt");
if (!fs.existsSync(reqFile)) {
  console.error(`${c("red", "✖")} requirements.txt not found at ${reqFile}`);
  process.exit(1);
}

const pipInstallCmd = pipCmd
  ? `${pipCmd} install -r "${reqFile}" --quiet`
  : `${pythonCmd} -m pip install -r "${reqFile}" --quiet`;

try {
  run(pipInstallCmd, { cwd: PROJECT_ROOT, stdio: "inherit" });
  ok("Python dependencies up-to-date");
} catch (e) {
  warn(`pip install had issues (${String(e.message || e).trim()}).  Trying with --user…`);
  try {
    run(`${pipCmd || `${pythonCmd} -m pip`} install -r "${reqFile}" --user --quiet`, { cwd: PROJECT_ROOT, stdio: "inherit" });
    ok("Python dependencies installed (--user)");
  } catch (e2) {
    console.error(`${c("red", "✖")} Failed to install Python dependencies.`);
    console.error("   Try manually: pip install -r requirements.txt");
    process.exit(1);
  }
}

// ---------------------------------------------------------------------------
// 4. Build frontend
// ---------------------------------------------------------------------------

step("Building frontend…");

const frontendDir = path.join(PROJECT_ROOT, "frontend");
const distDir = path.join(frontendDir, "dist");
const nodeModulesDir = path.join(frontendDir, "node_modules");

// Only rebuild if dist is stale or missing
let needBuild = !fs.existsSync(distDir);
if (!needBuild) {
  // Check if source files are newer than dist
  try {
    const distStat = fs.statSync(distDir);
    const srcDir = path.join(frontendDir, "src");
    if (fs.existsSync(srcDir)) {
      const newestSrc = findNewest(srcDir);
      if (newestSrc && newestSrc > distStat.mtimeMs) needBuild = true;
    }
  } catch { needBuild = true; }
}

// Install frontend npm deps ONLY if we need to build and they're missing.
// When dist/ is pre-built and up-to-date this step is skipped entirely,
// giving the user an instant first launch.
if (needBuild && !fs.existsSync(nodeModulesDir)) {
  log("Installing frontend npm dependencies…");
  try {
    run("npm install --prefer-offline --no-audit --no-fund", { cwd: frontendDir, stdio: "inherit" });
    ok("Frontend npm dependencies installed");
  } catch {
    warn("npm install had issues — will retry on next start");
  }
}

if (needBuild) {
  log("Running vite build…");
  try {
    run("npm run build", { cwd: frontendDir, stdio: "inherit" });
    ok("Frontend built");
  } catch {
    console.error(`${c("red", "✖")} Frontend build failed.`);
    process.exit(1);
  }
} else {
  ok("Frontend already built (dist/ is up-to-date)");
}

function findNewest(dir) {
  let newest = 0;
  try {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory() && entry.name !== "node_modules") {
        newest = Math.max(newest, findNewest(full));
      } else if (entry.isFile()) {
        newest = Math.max(newest, fs.statSync(full).mtimeMs);
      }
    }
  } catch { /* ignore permission errors */ }
  return newest;
}

// ---------------------------------------------------------------------------
// 5. Start services
// ---------------------------------------------------------------------------

console.log(`\n${c("green", "╔══════════════════════════════════════════╗")}`);
console.log(`${c("green", "║")}   ✨  AuroraCoder is starting…           ${c("green", "║")}`);
console.log(`${c("green", "╚══════════════════════════════════════════╝")}\n`);

const children = [];

// Environment for both Python processes
const env = {
  ...process.env,
  AURORACODER_DOCKER: "0",        // Tell config.py: NOT Docker
  AURORACODER_VNC: "0",           // No VNC desktop
  PYTHONUNBUFFERED: "1",
};

// Resolve the native launcher script (patches system prompt, then starts server)
const runNativePy = path.join(__dirname, "run_native.py");
if (!fs.existsSync(runNativePy)) {
  console.error(`${c("red", "✖")} run_native.py not found at ${runNativePy}`);
  process.exit(1);
}

// -- Backend (port 8080) --
step(`Starting agent backend (port ${backendPort})…`);
const backend = spawnProc(pythonCmd, [
  runNativePy, "backend", String(backendPort),
], {
  cwd: PROJECT_ROOT,
  env: { ...env, BACKEND_PORT: String(backendPort) },
});
children.push({ name: "Backend", proc: backend });

// Give backend a moment to start
log("Waiting for backend to be ready…");

// -- Gateway (port 8081 — serves frontend + API proxy) --
step(`Starting gateway (port ${gatewayPort})…`);
const gateway = spawnProc(pythonCmd, [
  runNativePy, "gateway", String(gatewayPort),
], {
  cwd: PROJECT_ROOT,
  env: {
    ...env,
    BACKEND_URL: `http://localhost:${backendPort}`,
    GATEWAY_PORT: String(gatewayPort),
  },
});
children.push({ name: "Gateway", proc: gateway });

// ---------------------------------------------------------------------------
// 6. Open browser (after a short delay for services to start)
// ---------------------------------------------------------------------------

const url = `http://localhost:${gatewayPort}`;
setTimeout(() => {
  console.log(`\n  ${c("green", "✔")} Opening ${c("bold", url)} in your browser…\n`);
  const opened = openBrowser(url);
  if (!opened) {
    log(`Could not open browser automatically.  Open ${url} manually.`);
  }
}, 2000);

// ---------------------------------------------------------------------------
// 7. Graceful shutdown
// ---------------------------------------------------------------------------

let shuttingDown = false;

function shutdown(signal) {
  if (shuttingDown) return;
  shuttingDown = true;
  console.log(`\n  ${c("yellow", signal)} received — shutting down…`);

  for (const { name, proc } of children.reverse()) {
    if (proc && !proc.killed) {
      try {
        proc.kill("SIGTERM");
      } catch { /* already dead */ }
    }
  }

  // Force-kill after 5s
  setTimeout(() => {
    for (const { name, proc } of children) {
      if (proc && !proc.killed) {
        try { proc.kill("SIGKILL"); } catch { /* */ }
      }
    }
    console.log(`  ${c("green", "✔")} AuroraCoder stopped.`);
    process.exit(0);
  }, 5000);
}

process.on("SIGINT", () => shutdown("SIGINT"));
process.on("SIGTERM", () => shutdown("SIGTERM"));

// Exit if any child dies unexpectedly
for (const { name, proc } of children) {
  proc.on("exit", (code, signal) => {
    if (!shuttingDown && (code !== 0 || signal)) {
      console.error(`\n  ${c("red", "✖")} ${name} exited (code=${code}, signal=${signal})`);
      shutdown("child-exit");
    }
  });
}

console.log(`  ${c("green", "✔")} Press Ctrl+C to stop all services.\n`);
