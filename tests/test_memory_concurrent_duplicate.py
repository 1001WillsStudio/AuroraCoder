from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from memory.ops.extractor import apply_extraction_plan
from memory.schema import MemoryItem
from memory.store import MemoryRepository


def test_concurrent_new_extraction_plans_store_one_identical_memory(tmp_path):
    candidate = {
        "content": "The local violet marker confirms readiness.",
        "description": "Violet readiness marker",
        "plane": "world",
        "type": "project",
        "scope": "project",
    }
    stores = [MemoryRepository(tmp_path), MemoryRepository(tmp_path)]
    both_ready = Barrier(2)

    def apply(number):
        both_ready.wait(timeout=3)
        plan = {**candidate, "description": f"Readiness note {number}"}
        return apply_extraction_plan([plan], f"session-{number}", "fixture", stores[number])[0]["id"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        found = list(pool.map(apply, (0, 1)))
    assert found[0] == found[1]
    assert [item.content for item in stores[0].all_items()] == [candidate["content"]]


def test_exact_duplicate_reuses_old_memory_and_keeps_scopes_and_expiry_separate(tmp_path):
    repo = MemoryRepository(tmp_path)
    original = repo.upsert(MemoryItem(
        content="Always verify ready.", description="Existing preference", plane="stance",
        type="preference", scope="user",
    ))
    repo.bump_usage([original.id])
    candidate = {"content": original.content, "description": "Newly worded description",
                 "plane": original.plane, "type": original.type, "scope": "user"}
    found = apply_extraction_plan([candidate], "second", "fixture", repo)
    assert found == [{"id": original.id, "source": "discovered"}]
    assert repo.get(original.id).usage_count == 1
    assert repo.get(original.id).description == original.description
    assert repo.get(original.id).corroboration_count == 0

    apply_extraction_plan([{**candidate, "scope": "project"}, {**candidate, "volatile": True, "ttl_days": 1}],
                          "third", "fixture", repo)
    assert len(repo.all_items()) == 3
