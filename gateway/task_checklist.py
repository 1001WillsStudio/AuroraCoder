"""Durable checklist state lives independently of the conversation transcript."""

import json
import logging
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from src.task_checklist import TaskEdit, TaskItem, TaskProgress, TaskResume, TaskStart

logger = logging.getLogger(__name__)


class TaskConflict(ValueError):
    pass


class TaskChecklist(BaseModel):
    id: str
    objective: str
    criteria: list[str]
    workspace: str
    conversation_ids: list[str] = Field(min_length=1)
    progress: TaskProgress
    status: Literal["paused", "active", "completed"] = "paused"
    reason: str = ""
    handoff: str = ""
    steering: str = ""
    request: str = ""
    options: TaskResume = Field(default_factory=TaskResume)
    started: bool = False
    stalled: int = 0
    revision: int = 1

    @property
    def conversation_id(self) -> str:
        return self.conversation_ids[-1]

    def public(self) -> dict:
        return {
            **self.model_dump(exclude={"conversation_ids", "options", "stalled", "handoff", "steering", "started", "request"}),
            "conversation_id": self.conversation_id,
        }

    def prompt_state(self) -> dict:
        return self.model_dump(include={
            "objective", "criteria", "workspace", "progress", "handoff", "steering", "request", "status",
        })


class TaskChecklistStore:
    """Gateway event-loop owned. Write the new state before replacing the in-memory state."""

    def __init__(self, directory: Path, write_json):
        self.directory = directory
        self.write_json = write_json
        self.by_conversation: dict[str, TaskChecklist] = {}
        for path in directory.glob("*.json"):
            try:
                task = TaskChecklist.model_validate_json(path.read_text(encoding="utf-8"))
                self.by_conversation.update(dict.fromkeys(task.conversation_ids, task))
            except (OSError, ValidationError):
                logger.exception("Could not load task checklist %s", path)

    def get(self, conversation_id: str) -> TaskChecklist | None:
        return self.by_conversation.get(conversation_id)

    def start(self, conversation_id: str, plan: TaskStart, workspace: str, request: str,
              options: TaskResume) -> TaskChecklist:
        if self.get(conversation_id):
            raise TaskConflict("A saved task already exists in this conversation.")
        return self._create(TaskChecklist(
            id=str(uuid.uuid4()), objective=plan.objective, criteria=[], workspace=workspace,
            conversation_ids=[conversation_id], progress=self._initial_progress(plan),
            status="active", request=request, options=options, started=True,
        ))

    def reopen(self, task: TaskChecklist, request: str, options: TaskResume,
               revised_plan: TaskStart | None) -> TaskChecklist:
        steering = "\n\n".join(filter(None, (task.steering, request)))[-6000:]
        changes = {
            "status": "active", "reason": "", "steering": steering, "started": True, "stalled": 0,
            "options": options if options.model_dump(exclude_none=True) else task.options,
            "progress": task.progress.model_copy(update={"blocker": ""}),
        }
        if revised_plan:
            changes.update(
                objective=revised_plan.objective, criteria=[], handoff="",
                progress=self._initial_progress(revised_plan),
            )
        return self.save(task, **changes)

    @staticmethod
    def _initial_progress(plan: TaskStart) -> TaskProgress:
        if len(set(plan.items)) != len(plan.items):
            raise TaskConflict("Give each checklist item a unique label.")
        return TaskProgress(items=[TaskItem(text=text) for text in plan.items])

    def _create(self, task: TaskChecklist) -> TaskChecklist:
        self.write_json(self.directory / f"{task.id}.json", task.model_dump())
        self.by_conversation[task.conversation_id] = task
        return task

    def save(self, task: TaskChecklist, **changes) -> TaskChecklist:
        updated = task.model_copy(update={**changes, "revision": task.revision + 1})
        self.write_json(self.directory / f"{updated.id}.json", updated.model_dump())
        self.by_conversation.update(dict.fromkeys(updated.conversation_ids, updated))
        for cid in set(task.conversation_ids) - set(updated.conversation_ids):
            self.by_conversation.pop(cid, None)
        return updated

    def edit(self, conversation_id: str, edit: TaskEdit, workspace: str) -> TaskChecklist:
        task = self.get(conversation_id)
        if task and (task.conversation_id != conversation_id or task.status == "active"):
            raise TaskConflict("Pause the task and edit it from its current conversation.")
        if (task.revision if task else None) != edit.revision:
            raise TaskConflict("The checklist has changed. Reload it before editing.")

        criteria = list(dict.fromkeys(edit.criteria))
        if task and task.objective == edit.objective and task.criteria == criteria:
            return task
        evidence = {
            item.text: item.evidence for item in task.progress.items
        } if task and task.objective == edit.objective else {}
        items = [
            TaskItem(text=text, evidence=evidence.get(text, ""))
            for text in criteria or [edit.objective[:500]]
        ]
        progress = TaskProgress(items=items)

        if task:
            return self.save(task, objective=edit.objective, criteria=criteria, progress=progress,
                             status="paused", reason="", handoff="", steering="", stalled=0)
        task = TaskChecklist(
            id=str(uuid.uuid4()), objective=edit.objective, criteria=criteria, workspace=workspace,
            conversation_ids=[conversation_id], progress=progress,
        )
        return self._create(task)

    def checkpoint(self, task: TaskChecklist, progress: TaskProgress) -> TaskChecklist:
        labels = {item.text for item in progress.items}
        if len(labels) != len(progress.items) or not set(task.criteria) <= labels:
            raise TaskConflict("Keep every user criterion verbatim and give each item a unique label.")
        if task.progress != progress:
            return self.save(task, progress=progress)
        return task

    def remove_conversation(self, conversation_id: str) -> None:
        task = self.get(conversation_id)
        if not task:
            return
        remaining = [cid for cid in task.conversation_ids if cid != conversation_id]
        if remaining:
            self.save(task, conversation_ids=remaining)
        else:
            (self.directory / f"{task.id}.json").unlink(missing_ok=True)
            self.by_conversation.pop(conversation_id, None)
