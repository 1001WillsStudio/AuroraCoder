# SWE-Bench Runner — Plan (Parallel Docker Workers, No Internet, Optional Frontend)

> Branch: `feature/swe-bench-runner`  (from `master` e06349e)

---

## 1. Design Decisions

| Decision | Rationale |
|---|---|
| **Multiple Docker containers** | Each worker is a full AuroraCoder container — own shell, workspace, persistence |
| **`--internal` network + port maps** | `--internal` drops the default gateway → container can't reach internet. Port mappings (`-p`) still work for host→container, so the runner reaches each worker via `localhost:<port>`. |
| **Optional frontend** | Map port 3000 per worker when `frontend_enabled: true`. Useful for early-stage verification — open a browser tab and watch the agent work live. |
| **Separate persistence volumes** | `AURORACODER_DATA_DIR=/swe_data` on per‑worker volumes — never touches `~/.auroracoder/` |
| **Git squashed to single base commit** | `rm -rf .git && git init && git add -A && git commit -m "base"` — agent can't `git log` to find the fix |
| **No `conv_type` special‑casing** | Plain `"user_chat"` |

---

## 2. Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                    swe_bench_runner.py  (runs on host)                 │
│                                                                       │
│   ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐      │
│   │ auroracoder-    │  │ auroracoder-    │  │ auroracoder-    │      │
│   │ swe-0           │  │ swe-1           │  │ swe-2           │  ... │
│   │ --network       │  │ --network       │  │ --network       │      │
│   │   swe-net       │  │   swe-net       │  │   swe-net       │      │
│   │ -p 8081:8081    │  │ -p 8082:8081    │  │ -p 8083:8081    │      │
│   │ -p 3001:3000 ◇  │  │ -p 3002:3000 ◇  │  │ -p 3003:3000 ◇  │      │
│   │                 │  │                 │  │                 │      │
│   │ gw  :8081       │  │ gw  :8081       │  │ gw  :8081       │      │
│   │ be  :8080       │  │ be  :8080       │  │ be  :8080       │      │
│   │ ws  :/workspace │  │ ws  :/workspace │  │ ws  :/workspace │      │
│   │ data:/swe_data  │  │ data:/swe_data  │  │ data:/swe_data  │      │
│   │ fe  :3000   ◇   │  │ fe  :3000   ◇   │  │ fe  :3000   ◇   │      │
│   └────────┬────────┘  └────────┬────────┘  └────────┬────────┘      │
│            │                    │                     │               │
│            └────────────────────┼─────────────────────┘               │
│                                 │                                     │
│              swe-net (--internal: no route to internet)                │
│                                 │                                     │
│   Runner reaches workers at:    │                                     │
│     http://localhost:8081  ← worker 0                                 │
│     http://localhost:8082  ← worker 1                                 │
│     http://localhost:8083  ← worker 2                                 │
│                                 │                                     │
│   To watch agent live:         │                                      │
│     http://localhost:3001  ← worker 0 frontend                        │
│                                 │                                     │
│                     asyncio.Queue<instance>                            │
│                     inst_0 → inst_1 → inst_2 → …                      │
│                                                                       │
│   Output:  swe_runs/<instance_id>/                                    │
│            ├── patch.diff                                             │
│            ├── conversation.json   (gateway-compatible)               │
│            └── metadata.json                                          │
└──────────────────────────────────────────────────────────────────────┘

◇ = only when frontend_enabled: true
```

---

## 3. How `--internal` + Port Maps Work

```
                    ┌──────────────────────────┐
                    │       swe-net             │
                    │    (--internal bridge)     │
                    │                           │
   Host  ──p 8081──▶│  auroracoder-swe-0 :8081  │──X──▶ internet
                    │                           │    (no default gw)
   Host  ──p 3001──▶│  auroracoder-swe-0 :3000  │
                    └──────────────────────────┘
```

- **Host → Container**: Docker port mapping (`-p`) injects DNAT rules into
  iptables that bypass the bridge's routing table.  `--internal` does not
  affect DNAT — host-to-container traffic works fine.
- **Container → Internet**: `--internal` removes the default gateway from
  the bridge.  The container has no route to `0.0.0.0/0`.  Outbound packets
  have nowhere to go.  This is enforced at the kernel routing level — the
  agent **cannot** reach the internet even if it tried.

---

## 4. Git Clone Strategy

Since workers have no internet, the **runner** (running on the host) does
all git operations, then copies files into the worker containers:

```bash
# Runner (host, has internet):
git clone --depth 1 https://github.com/<repo>.git /tmp/swe_clone_<id>
cd /tmp/swe_clone_<id>
git fetch --depth 1 origin <base_commit>
git checkout <base_commit>
rm -rf .git && git init && git add -A && git commit -m "base"

# Transfer into worker
docker cp /tmp/swe_clone_<id>/. auroracoder-swe-0:/workspace/
rm -rf /tmp/swe_clone_<id>

# Later, extract the patch:
docker exec auroracoder-swe-0 git -C /workspace diff HEAD
```

---

## 5. Container Startup

```bash
# Once:
docker network create --internal swe-net

# Per worker (worker 0):
docker run -d --name auroracoder-swe-0 \
    --network swe-net \
    -p 8081:8081 \                         # gateway → host
    -p 3001:3000 \                         # frontend → host (optional)
    -e WORKSPACE_DIR=/workspace \
    -e AURORACODER_DATA_DIR=/swe_data \
    -e ACCESS_PASSWORD=$ACCESS_PASSWORD \
    -e BACKEND_PORT=8080 \
    -e GATEWAY_PORT=8081 \
    -v swe_ws_0:/workspace \
    -v swe_data_0:/swe_data \
    auroracoder:latest
```

| Worker | Gateway (host) | Frontend (host, optional) |
|---|---|---|
| 0 | `localhost:8081` | `localhost:3001` |
| 1 | `localhost:8082` | `localhost:3002` |
| 2 | `localhost:8083` | `localhost:3003` |
| N | `localhost:{8081+N}` | `localhost:{3001+N}` |

---

## 6. File Layout

```
Aurora Coder/
├── swe_bench/
│   ├── __init__.py
│   ├── runner.py              # Pool manager + CLI entry point
│   ├── config.py              # Typed config
│   ├── worker.py              # Docker container lifecycle per worker
│   ├── gateway_client.py      # Async HTTP + SSE client
│   └── workspace.py           # Git ops on host, docker cp in/out
├── swe_bench_config.yaml
└── swe_bench_requirements.txt # httpx, pyyaml, docker
```

5 Python files.  No changes to existing code.

---

## 7. Component Details

### 7.1 `swe_bench/config.py`

```python
@dataclass
class RunnerConfig:
    # Dataset
    dataset_path:      str   = "data/swe-bench.jsonl"
    max_instances:     int   = 0                     # 0 = all

    # Parallelism
    workers:           int   = 4

    # Docker
    docker_image:      str   = "auroracoder:latest"
    container_prefix:  str   = "auroracoder-swe"
    network:           str   = "swe-net"             # --internal bridge
    volume_prefix:     str   = "swe"                 # → swe_ws_0, swe_data_0
    gateway_port_base: int   = 8081                  # host:container gateway port
    frontend_enabled:  bool  = False                 # set true to watch agent live
    frontend_port_base:int   = 3001                  # host:3000 container frontend

    # Agent
    provider:          str   = "deepseek"
    instance_timeout:  float = 1800.0                # 30 min

    # Output
    runs_dir:          str   = "swe_runs"
```

### 7.2 `swe_bench/worker.py`

```python
class Worker:
    """Manages one Docker container running AuroraCoder."""
    def __init__(self, worker_id: int, config: RunnerConfig):
        self.worker_id = worker_id
        self.name = f"{config.container_prefix}-{worker_id}"
        self.gateway_host_port = config.gateway_port_base + worker_id
        self.frontend_host_port = config.frontend_port_base + worker_id

    async def start(self):
        """docker run with --network swe-net -p ... Wait for /health."""

    async def stop(self):
        """docker stop + docker rm + docker volume rm."""

    async def run_instance(self, instance: dict) -> dict:
        """
        1. Clone repo on HOST, squash git, docker cp → /workspace
        2. POST http://localhost:{gateway_host_port}/api/chat → consume SSE
        3. docker exec <name> git -C /workspace diff HEAD → patch
        4. GET http://localhost:{gateway_host_port}/api/conversations/{id} → conv
        5. Save to swe_runs/{id}/
        6. Return result dict
        """

    @property
    def gateway_url(self) -> str:
        return f"http://localhost:{self.gateway_host_port}"

    @property
    def frontend_url(self) -> str | None:
        if not self.config.frontend_enabled:
            return None
        return f"http://localhost:{self.frontend_host_port}"
```

### 7.3 `swe_bench/runner.py`

```python
async def main():
    config = load_config()
    instances = load_dataset(config.dataset_path)
    if config.max_instances:
        instances = instances[:config.max_instances]

    ensure_network(config.network)            # docker network create --internal

    queue = asyncio.Queue()
    for inst in instances:
        queue.put_nowait(inst)

    workers = [Worker(i, config) for i in range(config.workers)]
    await asyncio.gather(*(w.start() for w in workers))

    if config.frontend_enabled:
        print("Frontend URLs:")
        for w in workers:
            print(f"  Worker {w.worker_id}: {w.frontend_url}")

    async def worker_loop(w: Worker):
        while True:
            try:
                inst = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            result = await w.run_instance(inst)
            log(w.worker_id, inst["instance_id"], result)

    await asyncio.gather(*(worker_loop(w) for w in workers))
    await asyncio.gather(*(w.stop() for w in workers))
    print_summary()
```

CLI:
```bash
python -m swe_bench.runner \
    --dataset data/swe-bench.jsonl \
    --workers 4 \
    --provider deepseek \
    --max 100 \
    --frontend-enabled          # expose :3001, :3002, … to watch live
```

### 7.4 `swe_bench/gateway_client.py`

```python
class GatewayClient:
    """Async HTTP/SSE client for one worker's gateway."""
    def __init__(self, base_url: str, timeout: float = 1800.0): ...

    async def chat(self, cid: str, message: str, provider: str) -> AsyncIterator[dict]:
        """POST /api/chat → yield SSE event dicts."""

    async def get_conversation(self, cid: str) -> dict:
        """GET /api/conversations/{cid} → gateway-compatible JSON."""

    async def cancel(self, cid: str) -> None:
        """POST /api/conversations/{cid}/cancel."""

    async def health(self) -> bool:
        """GET /health."""
```

### 7.5 `swe_bench/workspace.py`

```python
async def setup_repo(instance: dict) -> Path:
    """Clone repo on host, squash git to single base commit. Returns path."""

async def copy_to_container(host_path: Path, container_name: str):
    """docker cp <host_path>/. <container>:/workspace/"""

async def extract_patch(container_name: str) -> str:
    """docker exec <container> git -C /workspace diff HEAD"""

async def clear_workspace(container_name: str):
    """docker exec <container> rm -rf /workspace/*"""
```

---

## 8. Conversation Output

Gateway-compatible — drop into any AuroraCoder data dir and the frontend
displays it:

```json
{
  "id": "django__django-11001",
  "type": "user_chat",
  "status": "completed",
  "provider_id": "deepseek",
  "title": "…",
  "created_at": "…",
  "updated_at": "…",
  "messages": […]
}
```

Fetched via `GET http://localhost:{port}/api/conversations/{id}` and saved
to `swe_runs/{id}/conversation.json`.

---

## 9. Frontend for Early Verification

When `frontend_enabled: true`, the runner prints URLs before starting:

```
Frontend URLs:
  Worker 0: http://localhost:3001
  Worker 1: http://localhost:3002
  Worker 2: http://localhost:3003
  Worker 3: http://localhost:3004
```

Open any of these in a browser to watch that worker's agent work in real
time.  Great for debugging prompt strategies, checking tool usage, and
verifying the agent is on the right track before running a full batch.

Production runs use `frontend_enabled: false` to save resources.

---

## 10. Edge Cases

| Scenario | Handling |
|---|---|
| Container fails to start | Retry 3×, then fail the batch |
| Container crashes mid-instance | Catch, log, restart container, retry instance |
| Instance timeout | POST `/api/conversations/{id}/cancel`, save partial |
| Empty patch | Save empty diff, mark `no_changes` in metadata |
| Crash mid-batch | Skip instances with existing `conversation.json` in `swe_runs/` |
| Port already in use | Check `ss -tlnp` before `docker run` |
| `swe-net` already exists | `docker network inspect` → skip creation |

---

## 11. Implementation Order

| Step | Files | What |
|---|---|---|
| **M1** | `config.py`, `gateway_client.py`, `workspace.py` | Config, SSE client, git ops |
| **M2** | `worker.py` | Docker container lifecycle + full instance run |
| **M3** | `runner.py` | Pool manager, queue, CLI, frontend URLs |
| **M4** | `swe_bench_config.yaml`, `swe_bench_requirements.txt` | Polish |

---

## 12. Git Commit

```bash
git checkout -b feature/swe-bench-runner
git add swe_bench/ docs/swe-bench-plan.md swe_bench_config.yaml swe_bench_requirements.txt
git commit -m "feat(swe-bench): parallel batch runner with Docker workers

- N isolated Docker containers on --internal network (no internet)
- Port-mapped gateways (localhost:8081+N) for host interaction
- Optional frontend exposure (localhost:3001+N) for live verification
- Git squashed to single base commit to prevent git-history hacking
- Repos cloned on host, docker cp into no-network workers
- Separate swe_data volume per worker — never touches ~/.auroracoder/
- Async SSE gateway client + gateway-compatible conversation output
- Zero changes to existing core code

Co-authored-by: AuroraCoderAgent <aurorathesnowyfox@gmail.com>"
```
