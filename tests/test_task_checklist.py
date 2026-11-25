"""Exercise task ownership and recovery through the real gateway routes."""

import asyncio
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx

from gateway.api import app
from gateway import routes, streaming
from gateway.conversation_store import ConversationStore
from src.task_checklist import TaskEdit


def test_checkpoint_survives_context_rollover_pause_and_reload(tmp_path, monkeypatch):
    async def run():
        db = ConversationStore(tmp_path)
        monkeypatch.setattr(routes, "store", db)
        monkeypatch.setattr(streaming, "store", db)
        monkeypatch.setattr(streaming, "active_streams", {})
        monkeypatch.setattr(routes, "active_streams", streaming.active_streams)
        monkeypatch.setattr(streaming, "_run_session_end_memory_ops", lambda *_: None)
        monkeypatch.setattr(streaming, "WORKSPACE", tmp_path)
        first_cid = str(uuid.uuid4())
        objective = "Verify the complete desktop workflow."
        first_item = "The greeting renders."
        second_item = "The result survives a restart."
        evidence = "Opened the desktop page and observed the greeting."
        next_step = "Reopen the saved result and verify its contents."
        requests = []
        running = asyncio.Event()

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as api:
            async def checkpoint(body, progress, token=None):
                return await api.post(
                    f"/internal/task-checkpoint/{body['conversation_id']}",
                    headers={"X-Task-Token": token or body["task_token"]},
                    json=progress,
                )

            class Backend:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, *_):
                    pass

                @asynccontextmanager
                async def stream(self, _method, _url, json, **_kwargs):
                    requests.append(json)
                    segment = len(requests)

                    async def events():
                        if segment == 1:
                            progress = {
                                "items": [{"text": first_item, "evidence": evidence}, {"text": second_item}],
                                "checkpoint": next_step,
                            }
                            assert (await checkpoint(json, progress, "stale-token")).status_code == 409
                            assert (await checkpoint(json, {
                                "items": [{"text": first_item}], "replace_items": True,
                            })).status_code == 422
                            assert (await checkpoint(json, progress)).status_code == 200
                            status = "context_limit_reached"
                        elif segment == 2:
                            running.set()
                            await asyncio.Future()
                        else:
                            progress = json["task_state"]["progress"]
                            progress["items"][1]["evidence"] = "Restarted the app and read the same saved result."
                            assert (await checkpoint(json, progress)).status_code == 200
                            status = "completed"
                        yield streaming._format_sse("done", {
                            "conversation_id": json["conversation_id"],
                            "status": status,
                            "messages": [{"role": "assistant", "content": "Segment finished."}],
                            "raw_messages": [
                                {"role": "user", "content": json["message"]},
                                {"role": "assistant", "content": "Segment finished."},
                            ],
                        })

                    yield SimpleNamespace(status_code=200, aiter_text=events)

            monkeypatch.setattr(streaming, "httpx", SimpleNamespace(
                AsyncClient=lambda **_: Backend(), Timeout=httpx.Timeout, ConnectError=httpx.ConnectError,
            ))

            base = f"/api/conversations/{first_cid}"
            edit = await api.put(f"{base}/task", json={"objective": objective, "criteria": [first_item, second_item]})
            assert edit.status_code == 200
            assert edit.json()["task"]["status"] == "paused"

            start = await api.post(f"{base}/task/resume", json={"provider_id": "test", "model": "chosen-model"})
            assert start.json()["conversation_id"] == first_cid
            await asyncio.wait_for(running.wait(), timeout=3)

            task = (await api.get(f"{base}/task")).json()["task"]
            second_cid = task["conversation_id"]
            assert second_cid != first_cid
            assert task["id"] == edit.json()["task"]["id"]
            assert task["status"] == "active"
            assert requests[1]["messages"] == []
            assert requests[1]["provider_id"] == "test"
            assert requests[1]["model"] == "chosen-model"
            state = requests[1]["task_state"]
            assert state["objective"] == objective
            assert state["criteria"] == [first_item, second_item]
            assert state["progress"]["checkpoint"] == next_step
            assert state["progress"]["items"][0]["evidence"] == evidence

            replay = await api.get(f"{base}/stream")
            assert second_cid in replay.text
            assert "new_conversation_id" in replay.text
            cancel = await api.post(f"{base}/cancel")
            assert cancel.json()["task_conversation_id"] == second_cid
            assert (await checkpoint(requests[1], state["progress"])).status_code == 409

            reopened = ConversationStore(tmp_path)
            monkeypatch.setattr(routes, "store", reopened)
            monkeypatch.setattr(streaming, "store", reopened)
            current_path = f"/api/conversations/{second_cid}/task"
            paused = (await api.get(current_path)).json()["task"]
            assert paused["status"] == "paused"
            assert paused["progress"]["items"][0]["evidence"] == evidence

            resumed = await api.post(f"{current_path}/resume", json={})
            final_cid = resumed.json()["conversation_id"]
            assert final_cid != second_cid
            await asyncio.wait_for(streaming.active_streams[final_cid].task, timeout=3)
            completed = (await api.get(f"{base}/task")).json()["task"]
            assert completed["status"] == "completed"
            assert completed["conversation_id"] == final_cid
            assert all(item["evidence"] for item in completed["progress"]["items"])
            assert requests[2]["model"] == "chosen-model"

            deleted = await api.delete(f"/api/conversations/{final_cid}")
            assert deleted.status_code == 200
            assert (await api.get(f"{base}/task")).json()["task"]["conversation_id"] == second_cid

    asyncio.run(run())


def test_unverified_task_pauses_after_repeated_unchanged_segments(tmp_path, monkeypatch):
    db = ConversationStore(tmp_path)
    monkeypatch.setattr(streaming, "store", db)
    cid = db.create_conversation()
    task = db.tasks.edit(cid, TaskEdit(objective="Verify that the exported file can be opened."), str(tmp_path))
    task = db.tasks.save(task, status="active", stalled=1)
    stream = streaming.ActiveStream(
        cid, status="completed", task_token="active", initial_task_progress=task.progress,
    )

    streaming._finish_task_segment(stream, [], [])

    paused = db.tasks.get(cid)
    assert paused.status == "paused"
    assert paused.reason == "no_progress"
    assert paused.progress.items[0].evidence == ""
    assert stream.new_conversation_id is None


def test_normal_chat_can_start_and_resume_an_agent_owned_checklist(tmp_path, monkeypatch):
    async def run():
        db = ConversationStore(tmp_path)
        monkeypatch.setattr(routes, "store", db)
        monkeypatch.setattr(streaming, "store", db)
        monkeypatch.setattr(streaming, "WORKSPACE", tmp_path)
        monkeypatch.setattr(routes, "WORKSPACE", tmp_path)
        monkeypatch.setattr(streaming, "_schedule_memory_distillation", lambda *_: None)
        original = "Please verify that the landing page opens and that the exported file can be read."
        objective = "Verify the landing page and the export end to end."
        labels = ["Open the landing page.", "Read the exported file."]

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as api:
            class Backend:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, *_):
                    pass

                @asynccontextmanager
                async def stream(self, _method, _url, json, **_kwargs):
                    cid = json["conversation_id"]
                    user_message = json["message"]

                    async def call(action, args):
                        response = await api.post(
                            f"/internal/task-{action}/{cid}", json=args,
                            headers={"X-Task-Token": json["task_token"]},
                        )
                        assert response.status_code == 200, response.text
                        return response.json()["task_state"]

                    async def events():
                        if user_message == original:
                            assert "task_state" not in json
                            state = await call("start", {"objective": objective, "items": labels})
                            assert state["request"] == original
                            assert [item["text"] for item in state["progress"]["items"]] == labels
                            await call("checkpoint", {
                                "items": [{"text": labels[0], "evidence": "The page returned 200."},
                                          {"text": labels[1]}],
                                "blocker": "The export is unavailable.",
                            })
                        elif user_message == "Please continue. The export is now available.":
                            assert json["task_state"]["status"] == "paused"
                            state = await call("reopen", {})
                            assert state["request"] == original
                            assert state["steering"] == user_message
                            assert state["progress"]["items"][0]["evidence"] == "The page returned 200."
                            assert state["progress"]["blocker"] == ""
                            state["progress"]["items"][1]["evidence"] = "The exported file was read."
                            await call("checkpoint", state["progress"])
                        message = {"role": "user", "content": user_message}
                        yield streaming._format_sse("done", {
                            "conversation_id": cid, "status": "completed",
                            "messages": [message], "raw_messages": [message],
                        })

                    yield SimpleNamespace(status_code=200, aiter_text=events)

            monkeypatch.setattr(streaming, "httpx", SimpleNamespace(
                AsyncClient=lambda **_: Backend(), Timeout=httpx.Timeout, ConnectError=httpx.ConnectError,
            ))

            async def send(message, cid=None):
                response = await api.post("/api/chat", json={"message": message, "conversation_id": cid})
                assert response.status_code == 200, response.text
                return response.headers["X-Conversation-ID"]

            question_cid = await send("What is an export?")
            assert (await api.get(f"/api/conversations/{question_cid}/task")).json()["task"] is None

            cid = await send(original)
            task_url = f"/api/conversations/{cid}/task"
            paused = (await api.get(task_url)).json()["task"]
            assert paused["objective"] == objective
            assert paused["status"] == "paused"
            await send("What does the export contain?", cid)
            assert (await api.get(task_url)).json()["task"] == paused

            await send("Please continue. The export is now available.", cid)
            finished = (await api.get(task_url)).json()["task"]
            assert finished["id"] == paused["id"]
            assert finished["status"] == "completed"
            assert all(item["evidence"] for item in finished["progress"]["items"])

    asyncio.run(run())
