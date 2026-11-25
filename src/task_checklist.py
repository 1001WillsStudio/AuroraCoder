"""The task checklist protocol shared by the gateway and the agent backend."""

import json
import os
from typing import Annotated

import httpx
from pydantic import BaseModel, Field, StringConstraints

TaskText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=3000)]
ItemText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=3000)]


class TaskItem(BaseModel):
    text: ItemText
    evidence: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] = ""


class TaskProgress(BaseModel):
    items: list[TaskItem] = Field(min_length=1, max_length=20)
    checkpoint: Note = ""
    blocker: Note = ""

    @property
    def verified(self) -> bool:
        return not self.blocker and all(item.evidence for item in self.items)


class TaskCheckpointUpdate(BaseModel):
    items: list[TaskItem] = Field(
        default_factory=list, max_length=20, description="Checklist entries to add or update."
    )
    checkpoint: Note | None = None
    blocker: Note | None = None
    replace_items: bool = Field(
        default=False, description="Deliberately replace the plan with the supplied entries. Keep every user criterion."
    )
    reopen_items: list[ItemText] = Field(
        default_factory=list, max_length=20, description="Labels whose saved evidence is invalid and must be cleared."
    )


class TaskEdit(BaseModel):
    objective: TaskText
    criteria: list[ItemText] = Field(default_factory=list, max_length=20)
    revision: int | None = None


class TaskResume(BaseModel):
    provider: str | None = None
    provider_id: str | None = None
    model: str | None = None


class TaskStart(BaseModel):
    objective: TaskText
    items: list[ItemText] = Field(min_length=1, max_length=20)


class TaskReopen(BaseModel):
    revised_plan: TaskStart | None = None


class TaskRequestSlice(BaseModel):
    offset: int = Field(default=0, ge=0, description="Starting character, counted from zero.")
    max_chars: int = Field(default=3000, ge=1, le=3000)


START_TASK = "start_task"
RESUME_TASK = "resume_task"
CHECKPOINT_TOOL = "update_task_checklist"
READ_TASK_REQUEST = "read_task_request"
TASK_TOOL_NAMES = {START_TASK, RESUME_TASK, CHECKPOINT_TOOL, READ_TASK_REQUEST}
START_SCHEMA = {
    "type": "function",
    "function": {
        "name": START_TASK,
        "description": (
            "Record the objective and initial checklist for a multi-step or long-running task "
            "the user asked you to carry out. Decompose the user's request into a few verifiable "
            "outcomes and keep their acceptance conditions. Continue working after the checklist is saved."
        ),
        "parameters": TaskStart.model_json_schema(),
    },
}
RESUME_SCHEMA = {
    "type": "function",
    "function": {
        "name": RESUME_TASK,
        "description": (
            "Resume the saved task when the user asks to continue or steers ongoing work. "
            "Supply a revised plan only when the user explicitly changes the objective. "
            "Keep the saved checklist for an ordinary continuation."
        ),
        "parameters": TaskReopen.model_json_schema(),
    },
}
CHECKPOINT_SCHEMA = {
    "type": "function",
    "function": {
        "name": CHECKPOINT_TOOL,
        "description": (
            "Save changed checklist entries and a checkpoint. Omitted entries and their evidence are kept. "
            "Keep user criteria verbatim and explicitly reopen an item if its prior evidence no longer applies. "
            "An item is pending until its evidence records an actual check and observed result. "
            "Save a short checkpoint after material progress and before any context handoff. "
            "Use blocker only when work needs external input or cannot progress."
        ),
        "parameters": TaskCheckpointUpdate.model_json_schema(),
    },
}
REQUEST_SCHEMA = {
    "type": "function",
    "function": {
        "name": READ_TASK_REQUEST,
        "description": "Read the saved original task request by character range to recover details omitted from the prompt.",
        "parameters": TaskRequestSlice.model_json_schema(),
    },
}


class AgentTaskTools:
    """Expose the task tools for the current turn and refresh state after each saved call."""

    def __init__(self, conversation_id: str, token: str, state: dict | None):
        self.conversation_id = conversation_id
        self.token = token
        self.state = state

    @property
    def active(self) -> bool:
        return self.state is not None and self.state["status"] == "active"

    def schemas(self) -> list[dict]:
        if self.state is None:
            return [START_SCHEMA]
        if not self.active and self.state["status"] != "paused":
            return []
        tools = [CHECKPOINT_SCHEMA if self.active else RESUME_SCHEMA]
        if self.state.get("request_length"):
            tools.append(REQUEST_SCHEMA)
        return tools

    def prompt(self) -> str:
        if self.state is None:
            return (
                "When the user asks you to carry out a long-running task, a task likely to span context "
                "windows, or work with several distinct verifiable outcomes, call start_task early. "
                "Infer the objective and write the initial checklist yourself from the user's request. "
                "Keep each item tied to an outcome you can verify. Preserve explicit user constraints "
                "and keep the checklist as small as the work allows. Then proceed without waiting. "
                "For questions and small, self-contained requests, respond normally."
            )
        if self.active:
            instruction = (
                "Carry out the user's current task in the configured workspace. Respect the original "
                "request and the latest user direction. Continue from the first unverified item. "
                "Refine your checklist as you learn and use update_task_checklist to save progress "
                "before handing off context. Save the entries that changed and mark an item done only "
                "after observing concrete evidence. Once the objective and all items are "
                "verified, save the evidence and summarize. If work requires external input, record the blocker."
            )
        elif self.state["status"] == "paused":
            instruction = (
                "A task is paused. Call resume_task when the latest user message asks you to continue "
                "or provides direction for ongoing work. If the user asks a question or tells you to stop, "
                "respond without resuming the task. Use revised_plan only for an explicit change of goal."
            )
        elif self.state["status"] == "completed":
            instruction = "Use the completed task state as background for the user's current message."
        else:
            instruction = "This is an earlier segment of a task. Use the saved state only as background."
        if self.state["status"] in {"active", "paused"} and self.state.get("request_length"):
            instruction += " The original request is excerpted. Use read_task_request when you need any omitted detail."
        if self.active and self.state.get("terminal_jobs"):
            instruction += (
                " Check relevant terminal_jobs with terminal_job_status. They were already started in this task."
            )
        return instruction + "\n\n" + json.dumps(self.state, ensure_ascii=False)

    def call(self, name: str, arguments: dict) -> str:
        url = os.environ.get("GATEWAY_URL", "http://localhost:8081")
        path = {
            START_TASK: "task-start",
            RESUME_TASK: "task-reopen",
            CHECKPOINT_TOOL: "task-checkpoint",
            READ_TASK_REQUEST: "task-request",
        }[name]
        response = httpx.post(
            f"{url}/internal/{path}/{self.conversation_id}",
            headers={"X-Task-Token": self.token},
            json=arguments,
            timeout=10,
        )
        if response.is_error:
            return f"Task operation failed: {response.json().get('detail', response.status_code)}"
        result = response.json()
        if name == READ_TASK_REQUEST:
            text = result["text"]
            end = result["offset"] + len(text)
            output = f"Original request characters [{result['offset']}, {end}) of {result['total_chars']}.\n\n{text}"
            if result["next_offset"] is not None:
                output += f"\n\nRead from offset {result['next_offset']} to continue."
            return output
        self.state = result["task_state"]
        return {
            START_TASK: "Task and checklist saved. Proceed with the first item.",
            RESUME_TASK: "Task resumed. Proceed with the first unverified item.",
            CHECKPOINT_TOOL: "Task checklist saved.",
        }[name]
