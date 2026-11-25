"""Durable checklist state lives independently of the conversation transcript."""

import json
import logging
import re
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError
from src.message_provenance import TURN_INPUT_RECEIPT

from src.task_checklist import TaskCheckpointUpdate, TaskCriteriaChange, TaskEdit, TaskItem, TaskProgress, TaskResume, TaskStart
from gateway.attached_files import strip_attached_files
from gateway.task_instruction_display import strip_task_instruction

logger = logging.getLogger(__name__)
_SCOPE_LIMIT = 6000
_OMITTED = "\n\n[Middle of the user text omitted from this excerpt.]\n\n"
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?。！？\n])")
_CLAUSE_BOUNDARY = re.compile(r"[.,;!?，。；！？\n]+")
_CONDITIONAL = re.compile(r"(?:please\s+)?(?:if|unless|when|assuming)\b|如果|若|除非", re.I)
_LEADING_NEGATION = re.compile(
    r"(?:please\s+)?(?:do not|don['’]t|never|must not|should not|cannot|can['’]t|under no circumstances)\b"
    r"|(?:请)?(?:无论如何[，,\s]*)?(?:都)?(?:不要|不能|不得|禁止|不允许)", re.I,
)
_NEGATED_WAIVER = re.compile(
    r"\b(?:don't|do not|never|must not|should not|cannot)\b[^.,;\n]{0,30}"
    r"\b(?:skip|drop|remove|waive|omit|cancel|replace|change)\b"
    r"|(?:不要|不能|不得|禁止|不允许).{0,8}(?:跳过|撤回|取消|免除|更改|替换)", re.I,
)
_REPLACEMENT = re.compile(
    r"(?:please\s+)?(?:replace|change|switch|redefine)\s+(?:(?:the|current|original|previous|acceptance)\s+)*"
    r"(?:criteria|requirements|objective|goal|scope)\s+(?:with|to)\s+\S"
    r"|(?:请)?(?:(?:验收|目标|范围).{0,16}(?:改为|变更为|替换|重新定义)"
    r"|(?:替换|修改|更改|重设)(?:原有|原先|原来的)?(?:验收|目标|范围))", re.I,
)


def _scope_excerpt(text: str, limit: int = _SCOPE_LIMIT) -> str:
    if len(text) <= limit:
        return text
    available = limit - len(_OMITTED)
    lead = available // 2
    return text[:lead] + _OMITTED + text[-(available - lead):]


def _append_scope_excerpt(prior: str, text: str) -> str:
    separator = 2 if prior else 0
    if len(prior) + separator + len(text) > _SCOPE_LIMIT:
        prior = _scope_excerpt(prior, _SCOPE_LIMIT // 4)
        text = _scope_excerpt(text, _SCOPE_LIMIT - len(prior) - separator)
    return "\n\n".join(filter(None, (prior, text)))


def _directly_waives(clause: str, subject: str) -> bool:
    english_check = r"(?:check|verify|test|validate|run)"
    target = rf"(?:the\s+)?(?:{english_check}\s+)?{subject}(?:\s+(?:checks?|tests?|verification))?"
    action = r"(?:skip|drop|remove|waive|omit|cancel)"
    english = (
        rf"(?:(?:please|just)\s+)*(?:{action}\s+{target}"
        rf"|(?:there is )?no need to\s+{english_check}\s+{target}"
        rf"|{target}\s+(?:is |are )?(?:no longer required|not required|optional)(?: anymore)?)"
        r"(?:\s+(?:now|anymore|for this task))?"
    )
    check = r"(?:再)?(?:验收|验证|测试|检查|运行|跑)"
    waiver = r"(?:不用|无需|不必|不再需要|不再)"
    chinese = (
        rf"请?\s*(?:(?:跳过|撤回|取消|免除)\s*{subject}(?:的)?(?:\s*{check})?"
        rf"|{waiver}\s*{check}\s*{subject}|{subject}(?:的验收)?\s*{waiver}\s*{check}?)(?:了)?"
    )
    return re.fullmatch(english, clause, re.I) is not None or re.fullmatch(chinese, clause, re.I) is not None


class TaskConflict(ValueError):
    pass


class TaskSteeringEntry(BaseModel):
    content: str
    receipt: str | None = None
    applied_to: str | None = None

    def as_message(self) -> dict:
        message = {"role": "user", "content": self.content}
        if self.receipt is not None:
            message[TURN_INPUT_RECEIPT] = self.receipt
        return message


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
    latest_steering: str = ""
    steering_entries: list[TaskSteeringEntry] = Field(default_factory=list)
    terminal_jobs: list[str] = Field(default_factory=list)
    options: TaskResume = Field(default_factory=TaskResume)
    started: bool = False
    stalled: int = 0
    work_observations: list[str] = Field(default_factory=list)
    generation: int = 0
    revision: int = 1
    criteria_initialized: bool = False
    criteria_change_index: int = -1

    @property
    def conversation_id(self) -> str:
        return self.conversation_ids[-1]

    @property
    def verified(self) -> bool:
        return self.progress.verified and set(self.criteria) <= {item.text for item in self.progress.items}

    @property
    def steering_history(self) -> list[TaskSteeringEntry]:
        if not self.steering_entries and self.latest_steering:
            return [TaskSteeringEntry(content=self.latest_steering, applied_to=self.conversation_id)]
        return self.steering_entries

    @property
    def pending_steering(self) -> list[TaskSteeringEntry]:
        return [entry for entry in self.steering_history if entry.applied_to is None]

    @property
    def applied_steering(self) -> list[TaskSteeringEntry]:
        applied, _ = self.split_steering_at_pending()
        return applied

    def split_steering_at_pending(self) -> tuple[list[TaskSteeringEntry], list[TaskSteeringEntry]]:
        # A newer reply can be recorded behind a pending correction. Replay
        # the whole suffix in order so the pending correction cannot override it.
        history = self.steering_history
        boundary = next((i for i, entry in enumerate(history) if entry.applied_to is None), len(history))
        return history[:boundary], history[boundary:]

    def public(self) -> dict:
        return {
            **self.model_dump(exclude={
                "conversation_ids", "options", "stalled", "handoff", "steering", "started", "request", "latest_steering",
                "terminal_jobs", "steering_entries", "criteria_initialized", "criteria_change_index", "work_observations",
                "generation",
            }),
            "conversation_id": self.conversation_id,
        }

    def prompt_state(self) -> dict:
        state = self.model_dump(include={
            "objective", "criteria", "workspace", "progress", "handoff", "steering", "request", "status",
        })
        state["request"] = _scope_excerpt(self.request)
        applied, pending = self.split_steering_at_pending()
        if pending:
            state["steering"] = ""
            for entry in applied:
                state["steering"] = _append_scope_excerpt(state["steering"], entry.content)
        if applied:
            state["steering_count"] = len(applied)
        if len(self.request) > _SCOPE_LIMIT:
            state["request_length"] = len(self.request)
        if applied and applied[-1].content not in state["steering"]:
            state["latest_steering_length"] = len(applied[-1].content)
        if self.terminal_jobs:
            state["terminal_jobs"] = self.terminal_jobs
        return state


class TaskChecklistStore:
    """Gateway event-loop owned. Write the new state before replacing the in-memory state."""

    def __init__(self, directory: Path, write_json, read_messages: Callable[[str], list[dict]]):
        self.directory = directory
        self.write_json = write_json
        self.by_conversation: dict[str, dict[str, TaskChecklist]] = {}
        for path in directory.glob("*.json"):
            try:
                task = TaskChecklist.model_validate_json(path.read_text(encoding="utf-8"))
                if task.request and not task.criteria and not task.criteria_initialized:
                    criteria = self._recover_criteria(task, read_messages)
                    task = task.model_copy(update={"criteria": criteria})
                    if task.status == "completed" and not task.verified:
                        task = task.model_copy(update={"status": "paused", "reason": "criteria_recovered"})
                    # Keep already-recorded evidence, including legacy plans at the item limit.
                    items = task.progress.items
                    missing = [TaskItem(text=text) for text in criteria if text not in {item.text for item in items}]
                    if len(items) + len(missing) <= 20:
                        task = task.model_copy(update={"progress": task.progress.model_copy(
                            update={"items": [*items, *missing]},
                        )})
                self._index(task)
            except (OSError, ValidationError):
                logger.exception("Could not load task checklist %s", path)

    def get(self, conversation_id: str) -> TaskChecklist | None:
        tasks = self.by_conversation.get(conversation_id, {})
        return max(tasks.values(), key=lambda task: task.generation, default=None)

    def list_for_conversation(self, conversation_id: str) -> list[TaskChecklist]:
        return sorted(self.by_conversation.get(conversation_id, {}).values(),
                      key=lambda task: task.generation, reverse=True)

    def _index(self, task: TaskChecklist) -> None:
        for cid in task.conversation_ids:
            self.by_conversation.setdefault(cid, {})[task.id] = task

    def _unindex(self, conversation_id: str, task_id: str) -> None:
        tasks = self.by_conversation[conversation_id]
        tasks.pop(task_id)
        if not tasks:
            del self.by_conversation[conversation_id]

    @staticmethod
    def _recover_criteria(task: TaskChecklist, read_messages: Callable[[str], list[dict]]) -> list[str]:
        for cid in task.conversation_ids:
            calls = {}
            for message in read_messages(cid):
                if not isinstance(message, dict):
                    continue
                if message.get("role") == "assistant":
                    calls.update({
                        call.get("id"): call["function"].get("arguments")
                        for call in message.get("tool_calls") or []
                        if call.get("id") and call.get("function", {}).get("name") == "start_task"
                    })
                elif message.get("role") == "tool" and message.get("content") == (
                    "Task and checklist saved. Proceed with the first item."
                ):
                    arguments = calls.get(message.get("tool_call_id"))
                    if not isinstance(arguments, str):
                        continue
                    try:
                        plan = TaskStart.model_validate_json(arguments)
                    except ValidationError:
                        continue
                    return list(dict.fromkeys(plan.criteria or plan.items))
        return [item.text for item in task.progress.items]

    def start(self, conversation_id: str, plan: TaskStart, workspace: str, request: str,
              options: TaskResume) -> TaskChecklist:
        existing = self.get(conversation_id)
        if existing and (existing.status != "completed" or existing.conversation_id != conversation_id):
            raise TaskConflict("A saved task already exists in this conversation.")
        if any(criterion not in request for criterion in plan.criteria):
            raise TaskConflict("Quote each acceptance condition verbatim from the user's task request.")
        criteria = list(dict.fromkeys(plan.criteria or plan.items))
        return self._create(TaskChecklist(
            id=str(uuid.uuid4()), objective=plan.objective, criteria=criteria, workspace=workspace,
            conversation_ids=[conversation_id], progress=self._initial_progress(plan, criteria),
            status="active", request=request, options=options, started=True, criteria_initialized=True,
            generation=existing.generation + 1 if existing else 0,
        ))

    @staticmethod
    def _append_steering(task: TaskChecklist, entry: TaskSteeringEntry) -> dict:
        request = entry.content
        entries = task.steering_history
        if entry.receipt and any(saved.receipt == entry.receipt for saved in entries):
            return {}
        steering = _append_scope_excerpt(task.steering, request)
        return {"steering": steering, "latest_steering": request, "steering_entries": [*entries, entry]}

    def queue_steering(self, task: TaskChecklist, content: str, receipt: str) -> TaskChecklist:
        changes = self._append_steering(task, TaskSteeringEntry(content=content, receipt=receipt))
        return self.save(task, **changes) if changes else task

    @staticmethod
    def _criteria_amendment(task: TaskChecklist, change: TaskCriteriaChange | None) -> dict:
        if change is None:
            return {}
        history = task.steering_history
        index = change.steering_index if change.steering_index is not None else len(history) - 1
        if index < 0 or index >= len(history) or index < task.criteria_change_index:
            raise TaskConflict("Choose a saved steering entry at or after the last acceptance change.")
        entry = history[index]
        source = strip_attached_files(strip_task_instruction(entry.content))
        if not entry.applied_to or change.quote not in source:
            raise TaskConflict("Quote the actual user direction after it has reached this task.")
        # A short substring of a negated sentence is still part of that sentence.
        clauses = []
        for sentence in _SENTENCE_BOUNDARY.split(source):
            sentence = sentence.strip()
            if (sentence.endswith(("?", "？")) or _CONDITIONAL.match(sentence)
                    or _LEADING_NEGATION.match(sentence)):
                continue
            clauses.extend(clause for part in _CLAUSE_BOUNDARY.split(sentence)
                           if (clause := part.strip()) and clause in change.quote)
        if _NEGATED_WAIVER.search(change.quote):
            raise TaskConflict("The quoted direction prohibits removing or replacing requirements.")
        if change.replace and not any(_REPLACEMENT.match(clause) for clause in clauses):
            raise TaskConflict("Replacing the criteria requires an explicit user direction changing the scope.")
        if change.objective and (not change.replace or change.objective not in change.quote):
            raise TaskConflict("Quote the new objective verbatim in an explicit scope replacement.")
        for removal in change.withdraw:
            subject = re.escape(removal.subject)
            if removal.subject.isascii():
                subject = rf"\b{subject}\b"
            if (removal.criterion not in task.criteria
                    or not re.search(subject, removal.criterion, re.I)
                    or not any(_directly_waives(clause, subject) for clause in clauses)):
                raise TaskConflict("Quote the explicit withdrawal of each removed user criterion.")
        if any(label not in change.quote for label in change.add):
            raise TaskConflict("Quote the new acceptance conditions verbatim in the saved user direction.")
        withdrawn = {item.criterion for item in change.withdraw}
        keep = [] if change.replace else [label for label in task.criteria if label not in withdrawn]
        changes = {"criteria": list(dict.fromkeys([*keep, *change.add])), "criteria_change_index": index,
                   "criteria_initialized": True}
        if change.objective:
            changes.update(objective=change.objective, handoff="")
        return changes

    def reopen(self, task: TaskChecklist, request: str, options: TaskResume,
               revised_plan: TaskStart | None, *, receipt: str | None = None,
               criteria_change: TaskCriteriaChange | None = None) -> TaskChecklist:
        changes = {
            **self._append_steering(task, TaskSteeringEntry(
                content=request, receipt=receipt, applied_to=task.conversation_id,
            )),
            "status": "active", "reason": "",
            "started": True, "stalled": 0,
            "options": options if options.model_dump(exclude_none=True) else task.options,
            "progress": task.progress.model_copy(update={"blocker": ""}),
        }
        entered = task.model_copy(update=changes)
        amendment = self._criteria_amendment(entered, criteria_change)
        changes.update(amendment)
        criteria = changes.get("criteria", task.criteria)
        if revised_plan:
            changes.update(
                objective=changes.get("objective", revised_plan.objective), handoff="",
                progress=self._initial_progress(revised_plan, criteria),
            )
        elif amendment:
            retired = set(task.criteria) - set(criteria)
            items = {} if criteria_change.replace else {
                item.text: item for item in task.progress.items if item.text not in retired
            }
            items.update({label: TaskItem(text=label) for label in criteria if label not in items})
            if not 1 <= len(items) <= 20:
                raise TaskConflict("Provide a revised plan with one to twenty checklist items.")
            changes["progress"] = TaskProgress(items=list(items.values()))
        return self.save(task, **changes)

    @staticmethod
    def _initial_progress(plan: TaskStart, criteria: list[str]) -> TaskProgress:
        if len(set(plan.items)) != len(plan.items):
            raise TaskConflict("Give each checklist item a unique label.")
        labels = list(dict.fromkeys([*plan.items, *criteria]))
        if len(labels) > 20:
            raise TaskConflict("Keep no more than twenty checklist items including the user criteria.")
        return TaskProgress(items=[TaskItem(text=text) for text in labels])

    def _create(self, task: TaskChecklist) -> TaskChecklist:
        self.write_json(self.directory / f"{task.id}.json", task.model_dump())
        self._index(task)
        return task

    def save(self, task: TaskChecklist, **changes) -> TaskChecklist:
        updated = task.model_copy(update={**changes, "revision": task.revision + 1})
        self.write_json(self.directory / f"{updated.id}.json", updated.model_dump())
        self._index(updated)
        for cid in set(task.conversation_ids) - set(updated.conversation_ids):
            self._unindex(cid, task.id)
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
            return self.save(task, objective=edit.objective, criteria=criteria, progress=progress, criteria_initialized=True,
                             status="paused", reason="", handoff="", steering="", stalled=0)
        task = TaskChecklist(
            id=str(uuid.uuid4()), objective=edit.objective, criteria=criteria, workspace=workspace,
            conversation_ids=[conversation_id], progress=progress, criteria_initialized=True,
        )
        return self._create(task)

    def checkpoint(self, task: TaskChecklist, update: TaskCheckpointUpdate) -> TaskChecklist:
        labels = {item.text for item in update.items}
        if len(labels) != len(update.items):
            raise TaskConflict("Give each checklist item a unique label.")

        amendment = self._criteria_amendment(task, update.criteria_change)
        criteria = amendment.get("criteria", task.criteria)
        retired = set(task.criteria) - set(criteria)
        reset = update.criteria_change is not None and update.criteria_change.replace
        previous = {} if reset else {item.text: item for item in task.progress.items if item.text not in retired}
        items = {} if update.replace_items else previous.copy()
        for item in update.items:
            items[item.text] = previous.get(item.text, item) if not item.evidence else item
        if amendment:
            for label in criteria:
                items.setdefault(label, previous.get(label) or TaskItem(text=label))
        if not set(criteria) <= items.keys():
            raise TaskConflict("Keep every user criterion verbatim.")
        if not 1 <= len(items) <= 20:
            raise TaskConflict("Keep between one and twenty checklist items.")
        for label in update.reopen_items:
            if label not in items:
                raise TaskConflict(f"Cannot reopen an unknown checklist item: {label}")
            items[label] = items[label].model_copy(update={"evidence": ""})

        progress = TaskProgress(
            items=list(items.values()),
            checkpoint=task.progress.checkpoint if update.checkpoint is None else update.checkpoint,
            blocker=task.progress.blocker if update.blocker is None else update.blocker,
        )
        if task.progress != progress or amendment:
            return self.save(task, progress=progress, **amendment)
        return task

    def remove_conversation(self, conversation_id: str) -> None:
        for task in self.list_for_conversation(conversation_id):
            remaining = [cid for cid in task.conversation_ids if cid != conversation_id]
            if remaining:
                self.save(task, conversation_ids=remaining)
            else:
                (self.directory / f"{task.id}.json").unlink(missing_ok=True)
                self._unindex(conversation_id, task.id)
