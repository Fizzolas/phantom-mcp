"""Unit tests for the new PhantomMemory store."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from phantom.memory.store import PhantomMemory


@pytest.fixture
def mem(tmp_path: Path) -> PhantomMemory:
    return PhantomMemory(tmp_path / "phantom_memory")


def test_fact_roundtrip(mem):
    mem.fact_set("default_browser", "Firefox")
    r = mem.fact_get("default_browser")
    assert r["ok"] and r["value"] == "Firefox"
    assert "default_browser" in mem.fact_list()


def test_fact_search_word_overlap(mem):
    mem.fact_set("gpu", "NVIDIA RTX 4070 Laptop 8GB VRAM")
    mem.fact_set("cpu", "Intel i7-13620H 16 threads")
    r = mem.fact_search("nvidia gpu vram")
    assert r and r[0]["key"] == "gpu"
    assert r[0]["score"] > 0


def test_task_lifecycle(mem):
    mem.task_start("t1", "Open Notepad and write hello")
    mem.task_step("t1", "Launched notepad", ok=True, detail="PID 1234")
    mem.task_step("t1", "typed text", ok=True)
    mem.task_finish("t1", status="done", summary="hello.txt saved")
    r = mem.task_get("t1")
    assert r["ok"] and r["status"] == "done"
    assert len(r["steps"]) == 2
    assert mem.task_list()[0]["task_id"] == "t1"


def test_traces_and_failure_filter(mem):
    mem.trace_append("shell_cmd", {"command": "dir"}, ok=True, latency_ms=12)
    mem.trace_append("window_focus", {"title": "Foo"}, ok=False, error="not found", category="client_error")
    mem.trace_append("window_focus", {"title": "Bar"}, ok=False, error="still not found", category="client_error")

    recent = mem.trace_recent(limit=10)
    assert len(recent) == 3
    fails = mem.trace_failures(limit=10)
    assert len(fails) == 2
    only_focus = mem.trace_failures(tool="window_focus")
    assert len(only_focus) == 2 and only_focus[0]["tool"] == "window_focus"


def test_lesson_lifecycle(mem):
    mem.lesson_set("focus_first", "Always call window_list before window_focus")
    lessons = mem.lesson_list()
    assert any(l["name"] == "focus_first" for l in lessons)
    assert mem.lesson_get("focus_first")["body"].startswith("Always")
    mem.lesson_delete("focus_first")
    assert mem.lesson_get("focus_first") is None


def test_note_chunks_and_load(mem):
    big = "abc " * 5000  # ~20k chars
    r = mem.note_save("longread", big)
    assert r["ok"] and r["chunks"] >= 3
    first = mem.note_load("longread", index=0)
    assert first["ok"] and first["index"] == 0 and first["has_more"]
    listed = mem.note_list()
    assert any(n["label"] == "longread" for n in listed)
    mem.note_delete("longread")
    assert mem.note_load("longread") == {"ok": False, "error": "no note labeled 'longread'"}


@pytest.mark.asyncio
async def test_learn_from_traces_writes_lesson_for_repeated_failures(mem):
    for _ in range(3):
        mem.trace_append("flaky", {}, ok=False, error="boom", category="external_error")
    mem.trace_append("flaky", {}, ok=True)

    out = await mem.learn_from_traces(window=10)
    assert out["ok"] and "flaky" in out["lessons_written"]
    lesson = mem.lesson_get("auto:flaky")
    assert lesson and "flaky" in lesson["body"]


def test_compact_summarizes_old_traces(mem):
    for i in range(120):
        mem.trace_append("t", {"i": i}, ok=(i % 3 == 0))
    r = mem.compact()
    assert r["ok"] and r["trimmed"] is True
    # New trace count should be the configured keep window
    remaining = mem.trace_recent(limit=200)
    assert len(remaining) == mem.config.trace_keep_after_compact
    # A summary fact should have been written
    summaries = [k for k in mem.fact_list() if k.startswith("trace_summary:")]
    assert summaries
