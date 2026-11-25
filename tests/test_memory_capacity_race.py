from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, current_thread

from memory import settings
from memory.schema import MemoryItem
from memory.store import MemoryRepository


def test_two_capacity_checks_do_not_evict_more_than_the_overflow(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "max_memories", lambda: 50)
    repo = MemoryRepository(tmp_path)
    preference = repo.upsert(MemoryItem(
        plane="stance", type="preference", scope="user", description="Keep this preference", content="A preference."
    ))
    valuable = repo.upsert(MemoryItem(
        plane="world", type="reference", scope="project", description="Frequently used", content="Useful context.",
        usage_count=10,
    ))
    for label in ("Old one", "Old two"):
        repo.upsert(MemoryItem(
            plane="world", type="reference", scope="project", description=label, content=f"{label} details.",
        ))

    # Force the second legacy caller to read the victim list only after the
    # first caller removes the overflow. It has already read the old count.
    original_all_items = repo.all_items
    after_counts = Barrier(2)
    first_finished = Event()

    def read_after_count(**kwargs):
        after_counts.wait(timeout=3)
        if current_thread().name == "second-eviction":
            assert first_finished.wait(timeout=3)
        return original_all_items(**kwargs)

    def enforce(name):
        current_thread().name = name
        try:
            return repo.enforce_capacity(cap=2)
        finally:
            if name == "first-eviction":
                first_finished.set()

    monkeypatch.setattr(repo, "all_items", read_after_count)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(enforce, "first-eviction")
        second = pool.submit(enforce, "second-eviction")
        evicted = first.result(timeout=7) + second.result(timeout=7)

    assert evicted == 2
    assert {item.id for item in original_all_items()} == {preference.id, valuable.id}
