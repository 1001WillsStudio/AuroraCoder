package main

import (
	"encoding/json"
	"fmt"
	"log"
	"net"
	"net/http"
	"sync"
)

// ─── Progress server ──────────────────────────────────────────────────────

type progressServer struct {
	mu           sync.Mutex
	clients      map[chan string]struct{}
	port         int
	listener     net.Listener
	failed       bool
	errorMessage string
	instructed   bool
	infoMessage  string
}

func newProgressServer() *progressServer {
	ps := &progressServer{
		clients: make(map[chan string]struct{}),
		port:    8089,
	}
	return ps
}

func (ps *progressServer) url() string {
	return fmt.Sprintf("http://localhost:%d", ps.port)
}

// listen starts the HTTP server and blocks until the listener is ready.
func (ps *progressServer) listen() {
	mux := http.NewServeMux()
	mux.HandleFunc("/", ps.handlePage)
	mux.HandleFunc("/events", ps.handleSSE)

	var err error
	ps.listener, err = net.Listen("tcp", fmt.Sprintf(":%d", ps.port))
	if err != nil {
		// Port might be in use — try next port
		for alt := ps.port + 1; alt < ps.port+100; alt++ {
			ps.listener, err = net.Listen("tcp", fmt.Sprintf(":%d", alt))
			if err == nil {
				ps.port = alt
				break
			}
		}
		if err != nil {
			log.Printf("FATAL: Cannot start progress server: %v", err)
			return
		}
	}

	http.Serve(ps.listener, mux)
}

// broadcast sends a message to all connected SSE clients.
func (ps *progressServer) broadcast(event, data string) {
	payload := fmt.Sprintf("event: %s\ndata: %s\n\n", event, data)

	ps.mu.Lock()
	defer ps.mu.Unlock()

	for ch := range ps.clients {
		select {
		case ch <- payload:
		default:
			// Slow client — drop the message rather than blocking
		}
	}
}

// ─── Public API (called from main.go) ─────────────────────────────────────

func (ps *progressServer) setStep(step int, status string) {
	msg, _ := json.Marshal(map[string]interface{}{
		"step":   step,
		"status": status,
	})
	ps.broadcast("step", string(msg))
}

func (ps *progressServer) setStepMsg(step int, status string, message string) {
	msg, _ := json.Marshal(map[string]interface{}{
		"step":    step,
		"status":  status,
		"message": message,
	})
	ps.broadcast("step", string(msg))
}

func (ps *progressServer) warnStep(step int, message string) {
	msg, _ := json.Marshal(map[string]interface{}{
		"step":    step,
		"status":  "warning",
		"message": message,
	})
	ps.broadcast("step", string(msg))
}

func (ps *progressServer) logLine(line string) {
	// Escape JSON-unfriendly characters
	escaped, _ := json.Marshal(line)
	ps.broadcast("log", string(escaped))
}

func (ps *progressServer) done(url string) {
	msg, _ := json.Marshal(map[string]interface{}{
		"url": url,
	})
	ps.broadcast("done", string(msg))
}

func (ps *progressServer) fail(message string) {
	ps.mu.Lock()
	ps.failed = true
	ps.errorMessage = message
	ps.mu.Unlock()

	msg, _ := json.Marshal(map[string]interface{}{
		"message": message,
	})
	ps.broadcast("fail", string(msg))
}

func (ps *progressServer) instruction(message string) {
	ps.mu.Lock()
	ps.instructed = true
	ps.infoMessage = message
	ps.mu.Unlock()

	msg, _ := json.Marshal(map[string]interface{}{
		"message": message,
	})
	ps.broadcast("info", string(msg))
}

// ─── HTTP handlers ────────────────────────────────────────────────────────

func (ps *progressServer) handlePage(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Write([]byte(progressPageHTML))
}

func (ps *progressServer) handleSSE(w http.ResponseWriter, r *http.Request) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "Streaming not supported", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("Access-Control-Allow-Origin", "*")

	ch := make(chan string, 256)
	ps.mu.Lock()
	ps.clients[ch] = struct{}{}
	ps.mu.Unlock()

	defer func() {
		ps.mu.Lock()
		delete(ps.clients, ch)
		ps.mu.Unlock()
	}()

	// Send initial step definitions
	steps := []map[string]interface{}{
		{"step": 1, "label": "Checking Docker"},
		{"step": 2, "label": "Extracting project files"},
		{"step": 3, "label": "Checking configuration"},
		{"step": 4, "label": "Building base Docker image"},
		{"step": 5, "label": "Building app Docker image"},
		{"step": 6, "label": "Starting container"},
	}
	initMsg, _ := json.Marshal(steps)
	fmt.Fprintf(w, "event: init\ndata: %s\n\n", initMsg)
	flusher.Flush()

	// Replay any error/instruction that fired before this client connected
	ps.mu.Lock()
	if ps.failed {
		errMsg, _ := json.Marshal(map[string]interface{}{
			"message": ps.errorMessage,
		})
		fmt.Fprintf(w, "event: fail\ndata: %s\n\n", errMsg)
		flusher.Flush()
	}
	if ps.instructed {
		infoMsg, _ := json.Marshal(map[string]interface{}{
			"message": ps.infoMessage,
		})
		fmt.Fprintf(w, "event: info\ndata: %s\n\n", infoMsg)
		flusher.Flush()
	}
	ps.mu.Unlock()

	ctx := r.Context()
	for {
		select {
		case <-ctx.Done():
			return
		case msg := <-ch:
			fmt.Fprint(w, msg)
			flusher.Flush()
		}
	}
}

// ─── Embedded progress page ───────────────────────────────────────────────

const progressPageHTML = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AuroraCoder Setup</title>
<style>
  :root {
    --bg: #0f1117;
    --card: #1a1d27;
    --border: #2a2d3a;
    --text: #c9d1d9;
    --dim: #6e7681;
    --accent: #58a6ff;
    --green: #3fb950;
    --yellow: #d2991d;
    --red: #f85149;
    --log-bg: #0d1117;
  }
  * { margin:0; padding:0; box-sizing:border-box; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .container { width: 100%; max-width: 660px; padding: 24px; }
  h1 { font-size: 22px; font-weight: 600; margin-bottom: 4px; }
  .subtitle { color: var(--dim); font-size: 13px; margin-bottom: 28px; }

  .steps { list-style: none; margin-bottom: 20px; }
  .step {
    display: flex; align-items: center; gap: 12px;
    padding: 10px 0; border-bottom: 1px solid var(--border);
    transition: opacity 0.3s;
  }
  .step-icon {
    width: 28px; height: 28px; border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    font-size: 14px; flex-shrink: 0; border: 2px solid var(--border);
    background: transparent; transition: all 0.3s;
  }
  .step-icon.running {
    border-color: var(--accent);
    animation: pulse 1.2s infinite;
  }
  .step-icon.done    { border-color: var(--green); background: var(--green); color: #fff; }
  .step-icon.error   { border-color: var(--red); background: var(--red); color: #fff; }
  .step-icon.warning { border-color: var(--yellow); background: var(--yellow); color: #000; }
  @keyframes pulse {
    0%,100% { box-shadow: 0 0 0 0 rgba(88,166,255,0.4); }
    50%     { box-shadow: 0 0 0 8px rgba(88,166,255,0); }
  }
  .step-label { font-size: 14px; flex: 1; }
  .step-label small { display: block; color: var(--dim); font-size: 12px; margin-top: 2px; }

  .log-panel {
    background: var(--log-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px;
    height: 240px;
    overflow-y: auto;
    font-family: 'SF Mono', 'Cascadia Code', 'Consolas', monospace;
    font-size: 12px;
    line-height: 1.5;
    color: var(--dim);
  }
  .log-panel .line { white-space: pre-wrap; word-break: break-all; }
  .log-panel .line.highlight { color: var(--text); }

  .error-box {
    margin-top: 16px; padding: 16px; border-radius: 8px;
    background: rgba(248,81,73,0.1); border: 1px solid var(--red);
    display: none;
  }
  .error-box.show { display: block; }
  .error-box h3 { color: var(--red); font-size: 15px; margin-bottom: 8px; }
  .error-box pre { color: var(--text); font-size: 12px; white-space: pre-wrap; }

  .info-box {
    margin-top: 16px; padding: 16px; border-radius: 8px;
    background: rgba(88,166,255,0.1); border: 1px solid var(--accent);
    display: none;
  }
  .info-box.show { display: block; }
  .info-box h3 { color: var(--accent); font-size: 15px; margin-bottom: 8px; }
  .info-box pre { color: var(--text); font-size: 12px; white-space: pre-wrap; }

  .spinner {
    margin-top: 20px; display: flex; align-items: center; gap: 8px;
    color: var(--dim); font-size: 13px;
  }
  .spinner-dot {
    width: 8px; height: 8px; border-radius: 50%; background: var(--accent);
    animation: bounce 1.2s infinite;
  }
  .spinner-dot:nth-child(2) { animation-delay: 0.2s; }
  .spinner-dot:nth-child(3) { animation-delay: 0.4s; }
  @keyframes bounce {
    0%,80%,100% { transform: translateY(0); }
    40% { transform: translateY(-6px); }
  }

  .redirect-bar {
    margin-top: 16px; padding: 14px 16px; border-radius: 8px;
    background: rgba(63,185,80,0.1); border: 1px solid var(--green);
    display: none; align-items: center; gap: 10px;
  }
  .redirect-bar.show { display: flex; }
  .redirect-bar .icon { font-size: 24px; }
  .redirect-bar .msg { font-size: 14px; flex: 1; }
  .redirect-bar a {
    color: var(--accent); text-decoration: none; font-weight: 600;
    font-size: 14px;
  }
  .redirect-bar a:hover { text-decoration: underline; }
</style>
</head>
<body>
<div class="container">
  <h1>🚀 AuroraCoder Setup</h1>
  <p class="subtitle">One-click deployment — building and starting Docker containers</p>

  <ul class="steps" id="steps"></ul>

  <div class="log-panel" id="log">
    <div class="line">Waiting for tasks...</div>
  </div>

  <div class="spinner" id="spinner">
    <div class="spinner-dot"></div><div class="spinner-dot"></div><div class="spinner-dot"></div>
    <span>Working...</span>
  </div>

  <div class="error-box" id="errorBox">
    <h3>❌ Deployment Failed</h3>
    <pre id="errorMsg"></pre>
  </div>

  <div class="info-box" id="infoBox">
    <h3>ℹ️ Setup Required</h3>
    <pre id="infoMsg"></pre>
  </div>

  <div class="redirect-bar" id="redirectBar">
    <span class="icon">✅</span>
    <span class="msg">AuroraCoder is ready!</span>
    <a id="appLink" href="#">Open App →</a>
  </div>
</div>

<script>
const stepsEl = document.getElementById('steps');
const logEl = document.getElementById('log');
const spinnerEl = document.getElementById('spinner');
const errorBox = document.getElementById('errorBox');
const errorMsg = document.getElementById('errorMsg');
const redirectBar = document.getElementById('redirectBar');
const appLink = document.getElementById('appLink');

let stepStates = {};
let logLines = [];
let maxLogLines = 500;

function renderSteps() {
  stepsEl.innerHTML = Object.values(stepStates)
    .sort((a, b) => a.step - b.step)
    .map(s => {
      let icon = '○', cls = '';
      if (s.status === 'running') { icon = '◉'; cls = 'running'; }
      else if (s.status === 'done') { icon = '✓'; cls = 'done'; }
      else if (s.status === 'error') { icon = '✗'; cls = 'error'; }
      else if (s.status === 'warning') { icon = '⚠'; cls = 'warning'; }
      let msg = s.message ? '<small>' + escapeHtml(s.message).replace(/\n/g, '<br>') + '</small>' : '';
      return '<li class="step"><span class="step-icon ' + cls + '">' + icon + '</span><span class="step-label">' + escapeHtml(s.label) + msg + '</span></li>';
    }).join('');
}

function escapeHtml(str) {
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function addLog(line) {
  logLines.push(line);
  if (logLines.length > maxLogLines) logLines.shift();
  logEl.innerHTML = logLines.map(l => '<div class="line">' + escapeHtml(l) + '</div>').join('');
  logEl.scrollTop = logEl.scrollHeight;
}

// ── SSE connection ──────────────────────────────────────────────
const evtSource = new EventSource('/events');

evtSource.addEventListener('init', function(e) {
  const steps = JSON.parse(e.data);
  steps.forEach(s => {
    stepStates[s.step] = { step: s.step, label: s.label, status: 'pending', message: '' };
  });
  renderSteps();
});

evtSource.addEventListener('step', function(e) {
  const d = JSON.parse(e.data);
  if (!stepStates[d.step]) {
    stepStates[d.step] = { step: d.step, label: 'Step ' + d.step, status: d.status, message: '' };
  }
  stepStates[d.step].status = d.status;
  if (d.message) stepStates[d.step].message = d.message;
  renderSteps();
  if (d.status === 'warning') {
    addLog('⚠️  ' + (d.message || 'Step ' + d.step + ' needs attention'));
  }
});

evtSource.addEventListener('log', function(e) {
  const line = JSON.parse(e.data);
  addLog(line);
});

evtSource.addEventListener('done', function(e) {
  const d = JSON.parse(e.data);
  spinnerEl.style.display = 'none';
  redirectBar.classList.add('show');
  appLink.href = d.url;
  // Auto-redirect after 1 second
  setTimeout(function() { window.location.href = d.url; }, 1000);
});

evtSource.addEventListener('fail', function(e) {
  spinnerEl.style.display = 'none';
  try {
    const d = JSON.parse(e.data);
    errorMsg.textContent = d.message || 'Unknown error';
  } catch(_) {
    errorMsg.textContent = 'An unexpected error occurred. Check the terminal window for details.';
  }
  errorBox.classList.add('show');
});

evtSource.addEventListener('info', function(e) {
  spinnerEl.style.display = 'none';
  try {
    const d = JSON.parse(e.data);
    infoMsg.textContent = d.message || '';
  } catch(_) {
    infoMsg.textContent = 'Check the terminal window for details.';
  }
  infoBox.classList.add('show');
});

evtSource.onerror = function() {
  // SSE connection lost — if we haven't redirected or shown an error/info yet, show a note
  if (!redirectBar.classList.contains('show') && !errorBox.classList.contains('show') && !infoBox.classList.contains('show')) {
    addLog('⚠️  Lost connection to launcher process.');
  }
};
</script>
</body>
</html>`

