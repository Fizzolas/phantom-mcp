"""Tests that memory tools register cleanly and go through the registry end-to-end."""
from __future__ import annotations

from pathlib import Path

import pytest

import phantom.tools  # noqa: F401 — triggers @tool registration
from phantom.memory import adapter as adapter_mod
from phantom.tools._base import registry


@pytest.fixture(autouse=True)
def isolated_memory(tmp_path: Path, monkeypatch):
    """Point the module-level memory singleton at a fresh tmp dir per test."""
    from memory.manager import MemoryManager

    monkeypatch.setattr(adapter_mod, "_MANAGER", MemoryManager(tmp_path))


def test_memory_tools_are_registered():
    names = {t.name for t in registry.all()}
    expected = {
        "memory_save",
        "memory_get",
        "memory_delete",
        "memory_list",
        "memory_search",
        "task_start",
        "task_update",
        "task_load",
        "task_list",
    }
    missing = expected - names
    assert not missing, f"missing tools: {missing}"


def test_chunk_tools_are_NOT_registered():
    """PR 2 deliberately un-exposes chunk_* from the new tool surface."""
    names = {t.name for t in registry.all()}
    banned = {"chunk_save", "chunk_load", "chunk_reassemble", "chunk_list", "chunk_delete"}
    assert banned.isdisjoint(names), f"chunk tools leaked: {banned & names}"


@pytest.mark.asyncio
async def test_memory_save_then_get_via_registry():
    save = await registry.call("memory_save", {"key": "greeting", "value": "hello world"})
    assert save.ok is True

    got = await registry.call("memory_get", {"key": "greeting"})
    assert got.ok is True
    assert got.data["value"] == "hello world"


@pytest.mark.asyncio
async def test_memory_get_missing_is_client_error():
    got = await registry.call("memory_get", {"key": "nope"})
    assert got.ok is False
    assert got.meta["category"] == "client_error"


@pytest.mark.asyncio
async def test_memory_save_rejects_bad_schema():
    # missing required `value`
    r = await registry.call("memory_save", {"key": "k"})
    assert r.ok is False
    assert r.meta["category"] == "client_error"


@pytest.mark.asyncio
async def test_task_workflow_via_registry():
    start = await registry.call("task_start", {"task_id": "t1", "goal": "ship pr2"})
    assert start.ok is True

    upd = await registry.call(
        "task_update",
        {"task_id": "t1", "step": "wrote tools", "status": "in_progress"},
    )
    assert upd.ok is True

    loaded = await registry.call("task_load", {"task_id": "t1"})
    assert loaded.ok is True
    assert loaded.data["goal"] == "ship pr2"

    listed = await registry.call("task_list", {"status": "in_progress"})
    assert listed.ok is True
    assert any(t["task_id"] == "t1" for t in listed.data["tasks"])


@pytest.mark.asyncio
async def test_task_update_rejects_invalid_status_at_schema_layer():
    await registry.call("task_start", {"task_id": "t2", "goal": "x"})
    r = await registry.call(
        "task_update",
        {"task_id": "t2", "step": "s", "status": "nonsense"},
    )
    assert r.ok is False
    assert r.meta["category"] == "client_error"
