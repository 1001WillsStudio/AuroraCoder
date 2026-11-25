import json

import pytest

from gateway.conversation_store import ConversationStore
from gateway.task_checklist import TaskConflict
from src.task_checklist import TaskCheckpointUpdate, TaskCriteriaChange, TaskResume, TaskStart


def test_user_acceptance_survives_replanning_and_needs_its_own_evidence(tmp_path):
    store = ConversationStore(tmp_path)
    cid = store.create_conversation()
    criteria = ["Run on Linux", "Run on Windows"]
    task = store.tasks.start(cid, TaskStart(objective="Verify both builds", items=["Build"], criteria=criteria),
                             "/workspace", "Run on Linux. Run on Windows.", TaskResume())
    assert task.criteria == criteria
    with pytest.raises(TaskConflict, match="user criterion"):
        store.tasks.checkpoint(task, TaskCheckpointUpdate(replace_items=True, items=[
            {"text": criteria[0], "evidence": "Passed."},
        ]))
    task = store.tasks.reopen(task, "Continue", TaskResume(), TaskStart(objective="Build", items=["New step"]))
    assert task.criteria == criteria
    task = store.tasks.checkpoint(task, TaskCheckpointUpdate(replace_items=True, items=[
        {"text": criteria[0], "evidence": "Passed."}, {"text": criteria[1]},
    ]))
    assert not task.verified
    assert ConversationStore(tmp_path).tasks.get(cid).criteria == criteria


def test_legacy_completed_task_recovers_windows_from_successful_start_tool(tmp_path):
    store = ConversationStore(tmp_path)
    cid = store.create_conversation()
    plan = TaskStart(objective="Verify both", items=["Linux", "Windows"])
    task = store.tasks.start(cid, plan, "/workspace", "Verify on Linux and Windows.", TaskResume())
    store.save_messages(cid, [
        {"role": "assistant", "tool_calls": [
            {"id": "accepted", "function": {"name": "start_task", "arguments": plan.model_dump_json()}},
        ]},
        {"role": "tool", "tool_call_id": "accepted", "content": "Task and checklist saved. Proceed with the first item."},
    ])
    path = tmp_path / "tasks" / f"{task.id}.json"
    legacy = task.model_dump()
    legacy.update(criteria=[], status="completed", progress={"items": [{"text": "Linux", "evidence": "Passed"}]})
    legacy.pop("criteria_initialized", None)
    path.write_text(json.dumps(legacy), encoding="utf-8")
    repaired = ConversationStore(tmp_path).tasks.get(cid)
    assert repaired.status == "paused" and not repaired.verified
    assert [(row.text, row.evidence) for row in repaired.progress.items] == [("Linux", "Passed"), ("Windows", "")]


def test_actual_user_withdrawal_and_scope_replacement_change_protected_criteria(tmp_path):
    db = ConversationStore(tmp_path)
    cid = db.create_conversation()
    task = db.tasks.start(cid, TaskStart(objective="Verify", items=["Linux", "Windows"]),
                          "/workspace", "Verify Linux and Windows.", TaskResume())
    removal = [{"criterion": "Windows", "subject": "Windows"}]
    for source, quote in [
        ("Continue", "Continue"),
        ("Never drop Windows.", "drop Windows"),
        ("Skip Linux, keep Windows.", "Skip Linux, keep Windows."),
        ("No need to change the Windows check.", "No need to change the Windows check."),
        ("Remind me to say ‘skip Windows’.", "skip Windows"),
        ("Skip Windows?", "Skip Windows"),
        ("If Linux passes, skip Windows.", "skip Windows"),
    ]:
        with pytest.raises(TaskConflict, match="explicit withdrawal"):
            db.tasks.reopen(task, source, TaskResume(), None,
                            criteria_change=TaskCriteriaChange(quote=quote, withdraw=removal))
    directive = "Windows 不用验收了，只跑 Linux。"
    task = db.tasks.reopen(task, directive, TaskResume(), TaskStart(objective="Verify", items=["Linux"]),
                           criteria_change=TaskCriteriaChange(quote=directive, withdraw=removal))
    assert task.criteria == ["Linux"] and [item.text for item in task.progress.items] == ["Linux"]

    replacement = "Replace the acceptance criteria with Verify macOS."
    task = db.tasks.reopen(task, replacement, TaskResume(), None)
    task = db.tasks.checkpoint(task, TaskCheckpointUpdate(
        criteria_change=TaskCriteriaChange(quote=replacement, replace=True, add=["Verify macOS"]),
    ))
    assert task.criteria == ["Verify macOS"] and [item.text for item in task.progress.items] == ["Verify macOS"]
    reloaded = ConversationStore(tmp_path).tasks.get(cid)
    assert reloaded.criteria == ["Verify macOS"]

    new_objective = "Review the macOS installer"
    new_direction = f"Change the goal to {new_objective}."
    task = db.tasks.reopen(reloaded, new_direction, TaskResume(), None, criteria_change=TaskCriteriaChange(
        quote=new_direction, replace=True, add=[new_objective], objective=new_objective,
    ))
    assert task.objective == new_objective and task.criteria == [new_objective]


def test_a_partial_checkpoint_does_not_discard_a_previously_verified_item(tmp_path):
    store = ConversationStore(tmp_path)
    cid = store.create_conversation()
    task = store.tasks.start(cid, TaskStart(objective="Verify both fixtures", items=["First", "Second"]),
                             "/workspace", "Verify both fixtures", TaskResume())
    task = store.tasks.checkpoint(task, TaskCheckpointUpdate(
        items=[{"text": "First", "evidence": "The first command exited with status 0."},
               {"text": "Second"}],
        checkpoint="Look at the second fixture.",
    ))
    store.tasks.checkpoint(task, TaskCheckpointUpdate(
        items=[{"text": "Second", "evidence": "The second command exited with status 0."}],
    ))

    reloaded = ConversationStore(tmp_path).tasks.get(cid)
    assert [item.text for item in reloaded.progress.items] == ["First", "Second"]
    assert reloaded.progress.items[0].evidence == "The first command exited with status 0."
    assert reloaded.progress.checkpoint == "Look at the second fixture."
    assert reloaded.progress.verified


def test_checkpoint_only_clears_evidence_when_explicitly_reopened(tmp_path):
    store = ConversationStore(tmp_path)
    cid = store.create_conversation()
    task = store.tasks.start(cid, TaskStart(objective="Verify the fixture", items=["First"]),
                             "/workspace", "Verify the fixture", TaskResume())
    task = store.tasks.checkpoint(task, TaskCheckpointUpdate(
        items=[{"text": "First", "evidence": "Initial check"}, {"text": "Extra"}],
    ))
    task = store.tasks.checkpoint(task, TaskCheckpointUpdate(
        replace_items=True, items=[{"text": "First"}, {"text": "Replacement"}],
    ))
    assert [item.text for item in task.progress.items] == ["First", "Replacement"]
    assert task.progress.items[0].evidence == "Initial check"
    task = store.tasks.checkpoint(task, TaskCheckpointUpdate(reopen_items=["First"]))
    assert task.progress.items[0].evidence == ""
    with pytest.raises(TaskConflict, match="unknown checklist item"):
        store.tasks.checkpoint(task, TaskCheckpointUpdate(reopen_items=["Extra"]))
