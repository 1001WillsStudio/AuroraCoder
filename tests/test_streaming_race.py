"""
Simulate the race condition in _subscriber_sse that was fixed.

The bug: _subscriber_sse checked stream.finished BEFORE queue.get().
If the proxy's finally block set stream.finished=True before the
subscriber resumed from its yield, the 'done' event in the queue was
skipped.

Run:  cd /workspace/ThinkWithTool && python tests/test_streaming_race.py
"""

import asyncio


# ── Minimal simulacrum of the gateway streaming model ──────────────

class FakeStream:
    finished = False
    cancel_event = asyncio.Event()


async def proxy_task(stream, queue):
    """Simulate _proxy_backend_stream — puts 'done' then sets finished."""
    # Backend sends 'done' event → put it in subscriber queue
    queue.put_nowait(("done", {"status": "completed"}))
    # Backend connection closes → proxy finally block:
    stream.finished = True
    queue.put_nowait(None)  # sentinel


async def subscriber_OLD(stream, queue):
    """OLD (buggy) _subscriber_sse — checks stream.finished BEFORE queue.get()."""
    while True:
        if stream.finished:        # ← RACE: can break before draining queue
            return "missed_done"
        event = await asyncio.wait_for(queue.get(), timeout=0.1)
        if event is None:
            return "got_none"
        etype, edata = event
        if etype == "done":
            return "got_done"
        # In a real scenario, this yields. Here we just continue.
        await asyncio.sleep(0)  # simulate yield, give proxy task a turn


async def subscriber_NEW(stream, queue):
    """NEW (fixed) _subscriber_sse — queue.get() FIRST."""
    while True:
        event = await asyncio.wait_for(queue.get(), timeout=0.1)
        if event is None:
            return "got_none"
        etype, edata = event
        if etype == "done":
            return "got_done"
        await asyncio.sleep(0)  # simulate yield


async def test(scenario, subscriber_fn):
    """Run one scenario: proxy puts done, then enters finally."""
    stream = FakeStream()
    queue = asyncio.Queue(maxsize=256)

    proxy = asyncio.create_task(proxy_task(stream, queue))
    subscriber = asyncio.create_task(subscriber_fn(stream, queue))

    # Let both run. The proxy puts 'done' and sets finished=True in one
    # synchronous block, then puts None.  The subscriber resumes from
    # its await and either drains the queue or checks finished first.
    done, pending = await asyncio.wait(
        [proxy, subscriber], timeout=1.0, return_when=asyncio.ALL_COMPLETED
    )
    for t in pending:
        t.cancel()

    return subscriber.result()


async def main():
    # Run multiple trials — the race is deterministic here, but the
    # point is the structural bug.
    old_results = []
    new_results = []
    for _ in range(1):
        old_results.append(await test("OLD", subscriber_OLD))
        new_results.append(await test("NEW", subscriber_NEW))

    print(f"OLD (buggy):  {old_results}")
    print(f"NEW (fixed):  {new_results}")

    # The OLD subscriber should miss the 'done' event because
    # stream.finished is True before it can dequeue it.
    assert all(r == "missed_done" or r == "got_none" for r in old_results), \
        f"BUG FIX VERIFIED: OLD subscriber should NOT get 'done'. Got: {old_results}"
    # The NEW subscriber should get 'done' because it drains the queue first.
    assert all(r == "got_done" for r in new_results), \
        f"BUG FIX VERIFIED: NEW subscriber should get 'done'. Got: {new_results}"

    print("\n✓ Streaming race condition fix verified.")


if __name__ == "__main__":
    asyncio.run(main())
