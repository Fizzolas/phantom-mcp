"""Unit tests for phantom.memory.MemoryAdapter."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from phantom.memory.adapter import MemoryAdapter, CHUNK_THRESHOLD


@pytest.fixture
def adapter(tmp_path: Path) -> MemoryAdapter:
    from memory.manager import MemoryManager

    return MemoryAdapter(MemoryManager(tmp_path))


def test_save_and_get_small_value(adapter: MemoryAdapter):
    r = adapter.save("user.tz", "America/New_York")
    assert r["ok"] is True
    assert r["chunked"] is False

    g = adapter.get("user.tz")
    assert g["ok"] is True
    assert g["value"] == "America/New_York"
    assert g["chunked"] is False


def test_get_missing_key_returns_not_found(adapter: MemoryAdapter):
    g = adapter.get("never.saved")
    assert g["ok"] is False
    assert g["value"] is None
    assert g["reason"] == "not_found"


def test_delete_removes_key(adapter: MemoryAdapter):
    adapter.save("throwaway", "bye")
    d = adapter.delete("throwaway")
    assert d["ok"] is True
    assert d["existed"] is True

    # second delete is idempotent
    d2 = adapter.delete("throwaway")
    assert d2["existed"] is False


def test_list_keys_with_prefix(adapter: MemoryAdapter):
    adapter.save("user.name", "Alice")
    adapter.save("user.tz", "UTC")
    adapter.save("project.name", "phantom")

    all_keys = adapter.list_keys()
    assert set(all_keys) >= {"user.name", "user.tz", "project.name"}

    user_keys = adapter.list_keys(prefix="user.")
    assert set(user_keys) == {"user.name", "user.tz"}


def test_large_value_is_chunked_transparently(adapter: MemoryAdapter):
    big = "x" * (CHUNK_THRESHOLD * 3 + 50)
    r = adapter.save("big.blob", big)
    assert r["ok"] is True
    assert r["chunked"] is True
    assert r["chunks"] >= 3
    assert r["total_chars"] == len(big)

    g = adapter.get("big.blob")
    assert g["ok"] is True
    assert g["chunked"] is True
    assert g["value"] == big  # byte-exact round-trip


def test_chunk_pointer_is_opaque_to_callers(adapter: MemoryAdapter):
    """
    A caller should never see '__chunked__' or 'label' in the returned value.
    Those are implementation details of the adapter.
    """
    big = "y" * (CHUNK_THRESHOLD + 1)
    adapter.save("blob.y", big)
    g = adapter.get("blob.y")
    assert "__chunked__" not in g["value"]
    assert not g["value"].startswith("{")  # reassembled text, not json pointer


def test_delete_chunked_value_cleans_up_chunks(adapter: MemoryAdapter):
    big = "z" * (CHUNK_THRESHOLD * 2 + 100)
    adapter.save("blob.z", big)

    # Legacy chunks for this key should exist before delete
    legacy = adapter._m  # type: ignore[attr-defined]
    chunks_before = [k for k in legacy.raw_keys() if k.startswith("chunk:auto:blob.z")]
    assert len(chunks_before) >= 1

    adapter.delete("blob.z")

    chunks_after = [k for k in legacy.raw_keys() if k.startswith("chunk:auto:blob.z")]
    assert chunks_after == []


def test_search_finds_by_content(adapter: MemoryAdapter):
    adapter.save("lunch", "pizza margherita with fresh basil and mozzarella")
    adapter.save("dinner", "grilled salmon with lemon and asparagus")
    adapter.save("breakfast", "eggs and toast")

    hits = adapter.search("salmon", limit=5)
    assert any(h["key"] == "dinner" for h in hits)


def test_search_empty_query_returns_empty_list(adapter: MemoryAdapter):
    adapter.save("x", "something")
    assert adapter.search("") == []
    assert adapter.search("   ") == []


def test_task_state_machine(adapter: MemoryAdapter):
    adapter.task_start("refactor-pr2", "Add memory adapter")
    adapter.task_update("refactor-pr2", "wrote adapter", status="in_progress")
    adapter.task_update("refactor-pr2", "added tests", status="in_progress", summary="on track")
    adapter.task_update("refactor-pr2", "done", status="done", summary="shipped")

    loaded = adapter.task_load("refactor-pr2")
    assert loaded["status"] == "done"
    assert loaded["summary"] == "shipped"
    assert len(loaded["steps"]) == 3


def test_task_update_rejects_invalid_status(adapter: MemoryAdapter):
    adapter.task_start("t1", "goal")
    with pytest.raises(ValueError):
        adapter.task_update("t1", "step", status="nonsense")


def test_task_list_filters_by_status(adapter: MemoryAdapter):
    adapter.task_start("a", "goal a")
    adapter.task_start("b", "goal b")
    adapter.task_update("b", "done", status="done")

    in_progress = adapter.task_list(status="in_progress")
    done = adapter.task_list(status="done")

    assert {t["task_id"] for t in in_progress} == {"a"}
    assert {t["task_id"] for t in done} == {"b"}


def test_save_rejects_non_string_value(adapter: MemoryAdapter):
    with pytest.raises(TypeError):
        adapter.save("k", 123)  # type: ignore[arg-type]


def test_save_rejects_empty_key(adapter: MemoryAdapter):
    with pytest.raises(ValueError):
        adapter.save("", "v")
