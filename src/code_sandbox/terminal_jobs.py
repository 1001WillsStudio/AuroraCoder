"""Look up the bounded output and status of commands detached by the terminal."""

import json
import re
import sys
import tempfile
import time
from pathlib import Path


_JOB_ID = re.compile(r"[0-9a-f]{32}\Z")
_TAIL_BYTES = 6_000
_MAX_WAIT_SECONDS = 30


def job_paths(job_id: str, background: bool) -> tuple[Path, Path]:
    root = Path(tempfile.gettempdir())
    output = root / "agent_bg_logs" / f"bg_{job_id}.log" if background else root / f"shell_out_{job_id}.txt"
    return output, output.with_suffix(".status")


def _record_path(job_id: str) -> Path:
    return Path(tempfile.gettempdir()) / f"aurora_terminal_job_{job_id}.json"


def _process_start(pid: int) -> str | None:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return fields[19] if fields[0] not in {"Z", "X"} else None
    except (OSError, IndexError):
        return None


def remember_terminal_job(job_id: str, background: bool, pid: int) -> str | None:
    """Publish the job handle after the shell reports its process."""
    try:
        marker = _process_start(pid) if sys.platform == "linux" else None
        _record_path(job_id).write_text(
            json.dumps({"background": background, "pid": pid, "start": marker}), encoding="utf-8",
        )
        return job_id
    except OSError:
        return None


def _exit_code(path: Path) -> int | None:
    try:
        with path.open("rb") as status:
            value = status.read(24).strip()
            return int(value) if re.fullmatch(rb"-?\d+", value) else None
    except OSError:
        return None


def terminal_job_status(job_id: str, wait_seconds: int = 0) -> str:
    if not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id):
        return "Error: supply the Job ID returned by a terminal command."
    if type(wait_seconds) is not int or not 0 <= wait_seconds <= _MAX_WAIT_SECONDS:
        return f"Error: wait_seconds must be an integer between 0 and {_MAX_WAIT_SECONDS}."
    try:
        with _record_path(job_id).open("rb") as source:
            record = json.loads(source.read(512))
        background = record["background"]
        pid = record["pid"]
        marker = record["start"]
        if type(background) is not bool or type(pid) is not int or pid <= 0:
            raise ValueError("Invalid job record")
    except (OSError, KeyError, TypeError, ValueError):
        return f"Status: unknown\nJob ID: {job_id}\nThis terminal job is unavailable in this Docker instance."

    output_path, status_path = job_paths(job_id, background)
    deadline = time.monotonic() + wait_seconds
    while True:
        code = _exit_code(status_path)
        if code is not None:
            status = f"completed\nExit Code: {code}"
            break
        if sys.platform == "linux" and (marker is None or _process_start(pid) != marker):
            code = _exit_code(status_path)  # Check the final status again after observing exit.
            status = f"completed\nExit Code: {code}" if code is not None else "ended without a recorded exit code"
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            status = "running"
            break
        time.sleep(min(remaining, 0.2))

    result = [f"Status: {status}", f"Job ID: {job_id}", f"Output file: {output_path}"]
    try:
        with output_path.open("rb") as output:
            size = output.seek(0, 2)
            output.seek(max(0, size - _TAIL_BYTES))
            tail = output.read(_TAIL_BYTES).decode("utf-8", errors="replace")
        if tail:
            result.extend(["Recent output:", tail])
    except OSError:
        pass
    return "\n".join(result)
