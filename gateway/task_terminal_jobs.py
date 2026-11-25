"""Retain live job references independently of the model's last checkpoint."""

import re
from collections.abc import Set


_JOB_ID = re.compile(r"^Job ID: ([a-f0-9]{32})$", re.MULTILINE)
_MAX_JOBS = 12
_FINISHED = {"Status: completed", "Status: ended without a recorded exit code", "Status: unknown"}


def pending_terminal_jobs(
    previous: list[str], messages: list[dict], prior_tool_ids: Set[str] = frozenset(),
) -> list[str]:
    jobs = dict.fromkeys(previous)
    calls = {}
    for message in messages:
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                name = call.get("function", {}).get("name")
                call_id = call.get("id")
                if name in {"run_terminal_command", "terminal_job_status"} and call_id and call_id not in prior_tool_ids:
                    calls[call_id] = name
        elif message.get("role") == "tool":
            name = calls.get(message.get("tool_call_id"))
            output = message.get("content")
            if not name or not isinstance(output, str):
                continue
            status = output.partition("\n")[0]
            marker = _JOB_ID.search(output)
            if not marker:
                continue
            job_id = marker.group(1)
            if name == "run_terminal_command" and status == "Status: running":
                jobs[job_id] = None
            elif name == "terminal_job_status" and status in _FINISHED:
                jobs.pop(job_id, None)
    return list(jobs)[-_MAX_JOBS:]
