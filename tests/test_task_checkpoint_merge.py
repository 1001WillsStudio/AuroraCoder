import pytest

from gateway.conversation_store import ConversationStore
from gateway.task_checklist import TaskConflict
from src.task_checklist import TaskCheckpointUpdate, TaskResume, TaskStart


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
