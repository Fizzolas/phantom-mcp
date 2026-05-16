"""
phantom.tools.memory — memory tools the LM Studio / Jan.ai model uses directly.

Phase 2 fixes applied:
  - All tool functions that call async store methods are now `async def`.
    Previously they were sync `def` calling async methods via
    `asyncio.get_event_loop().run_until_complete()` which breaks inside
    an already-running event loop (the MCP server's loop).
  - memory_learn_from_traces now forwards _LMS_INFO model id so lessons
    are attributed to the real loaded model, not the "local-model" stub.
  - memory_compact returns the full stats dict (kept + compacted counts).
  - memory_task_step: fixed shadowed `ok` name collision with contracts.ok.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from phantom.contracts import ok as _ok, fail
from phantom.tools._base import tool
from phantom.memory.store import PhantomMemory


# ---- module-level singleton -------------------------------------------------
_memory: Optional[PhantomMemory] = None


def get_memory() -> PhantomMemory:
    """Return the process-wide PhantomMemory, creating it if needed."""
    global _memory
    if _memory is None:
        repo_root = Path(__file__).resolve().parents[2]
        _memory = PhantomMemory(repo_root / "data" / "phantom_memory")
    return _memory


def set_memory(mem: PhantomMemory) -> None:
    """Wire a custom store (used by the server entry point at boot)."""
    global _memory
    _memory = mem


# ---- schemas ----------------------------------------------------------------
class FactSetIn(BaseModel):
    key: str = Field(..., min_length=1, max_length=200)
    value: str = Field(..., max_length=20_000)
    model_config = ConfigDict(extra="forbid")


class FactKeyIn(BaseModel):
    key: str = Field(..., min_length=1, max_length=200)
    model_config = ConfigDict(extra="forbid")


class FactSearchIn(BaseModel):
    query: str = Field(..., min_length=1, max_length=400)
    top_k: int = Field(8, ge=1, le=50)
    model_config = ConfigDict(extra="forbid")


class TaskStartIn(BaseModel):
    task_id: str = Field(..., min_length=1, max_length=120)
    goal: str = Field(..., min_length=1, max_length=2000)
    model_config = ConfigDict(extra="forbid")


class TaskStepIn(BaseModel):
    task_id: str = Field(..., min_length=1, max_length=120)
    step: str = Field(..., min_length=1, max_length=2000)
    ok: bool = True
    detail: str = Field("", max_length=4000)
    model_config = ConfigDict(extra="forbid")


class TaskFinishIn(BaseModel):
    task_id: str = Field(..., min_length=1, max_length=120)
    status: str = Field("done", max_length=40)
    summary: str = Field("", max_length=4000)
    model_config = ConfigDict(extra="forbid")


class TaskGetIn(BaseModel):
    task_id: str = Field(..., min_length=1, max_length=120)
    model_config = ConfigDict(extra="forbid")


class TaskListIn(BaseModel):
    limit: int = Field(20, ge=1, le=100)
    model_config = ConfigDict(extra="forbid")


class TraceRecentIn(BaseModel):
    limit: int = Field(20, ge=1, le=200)
    tool: str = Field("", max_length=120)
    model_config = ConfigDict(extra="forbid")


class LessonSetIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    body: str = Field(..., min_length=1, max_length=4000)
    model_config = ConfigDict(extra="forbid")


class LessonNameIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    model_config = ConfigDict(extra="forbid")


class NoteSaveIn(BaseModel):
    label: str = Field(..., min_length=1, max_length=120)
    text: str = Field(..., max_length=2_000_000)
    model_config = ConfigDict(extra="forbid")


class NoteLoadIn(BaseModel):
    label: str = Field(..., min_length=1, max_length=120)
    index: int = Field(0, ge=0)
    model_config = ConfigDict(extra="forbid")


class NoteLabelIn(BaseModel):
    label: str = Field(..., min_length=1, max_length=120)
    model_config = ConfigDict(extra="forbid")


class NoneIn(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LearnIn(BaseModel):
    window: int = Field(200, ge=10, le=2000)
    use_lms: bool = Field(False, description="If true, ask the loaded model to phrase lessons.")
    lms_base: str = Field(
        "http://localhost:1234/v1",
        description="OpenAI-compatible endpoint (LM Studio or Jan.ai).",
    )
    model_config = ConfigDict(extra="forbid")


class CompactIn(BaseModel):
    target_chars: int = Field(
        20_000,
        ge=1024,
        le=1_000_000,
        description=(
            "Advisory size hint (accepted for API compatibility). "
            "Actual cutoff is record-count based via trace_keep_after_compact config."
        ),
    )
    model_config = ConfigDict(extra="forbid")


# ---- facts ------------------------------------------------------------------
@tool("memory_fact_set", category="memory", schema=FactSetIn, timeout_s=5.0)
async def memory_fact_set(key: str, value: str) -> dict:
    """
    Save a durable fact under `key`. Use for things you want to remember
    across sessions: "default browser", "user's main work directory",
    "GPU is RTX 4070 8GB", etc.
    """
    return _ok(await get_memory().fact_set(key, value))


@tool("memory_fact_get", category="memory", schema=FactKeyIn, timeout_s=3.0)
def memory_fact_get(key: str) -> dict:
    """Read a previously saved fact by key."""
    r = get_memory().fact_get(key)
    return _ok(r) if r.get("ok") else fail(r["error"], category="client_error")


@tool("memory_fact_delete", category="memory", schema=FactKeyIn, timeout_s=3.0)
async def memory_fact_delete(key: str) -> dict:
    """Delete a fact."""
    r = await get_memory().fact_delete(key)
    return _ok(r) if r.get("ok") else fail(r["error"], category="client_error")


@tool("memory_fact_list", category="memory", schema=NoneIn, timeout_s=3.0)
def memory_fact_list() -> dict:
    """List all stored fact keys."""
    return _ok({"keys": get_memory().fact_list()})


@tool("memory_fact_search", category="memory", schema=FactSearchIn, timeout_s=5.0)
def memory_fact_search(query: str, top_k: int = 8) -> dict:
    """
    Search stored facts by word overlap. Returns top matches with key,
    score, and a 300-char preview. Use this to recall something without
    knowing the exact key.
    """
    return _ok({"matches": get_memory().fact_search(query, top_k=top_k)})


# ---- tasks ------------------------------------------------------------------
@tool("memory_task_start", category="memory", schema=TaskStartIn, timeout_s=3.0)
async def memory_task_start(task_id: str, goal: str) -> dict:
    """
    Start a task and record its goal. Use one task_id per user request so
    you can later look back at what you did.
    """
    return _ok(await get_memory().task_start(task_id, goal))


@tool("memory_task_step", category="memory", schema=TaskStepIn, timeout_s=3.0)
async def memory_task_step(
    task_id: str, step: str, ok: bool = True, detail: str = ""
) -> dict:
    """Append a step to a task: what you did and whether it worked."""
    # Note: parameter `ok` shadows the import; use the store method directly.
    return _ok(await get_memory().task_step(task_id, step, ok=ok, detail=detail))


@tool("memory_task_finish", category="memory", schema=TaskFinishIn, timeout_s=3.0)
async def memory_task_finish(
    task_id: str, status: str = "done", summary: str = ""
) -> dict:
    """Mark a task done/failed/blocked with an optional summary."""
    r = await get_memory().task_finish(task_id, status=status, summary=summary)
    return _ok(r) if r.get("ok") else fail(r["error"], category="client_error")


@tool("memory_task_get", category="memory", schema=TaskGetIn, timeout_s=3.0)
def memory_task_get(task_id: str) -> dict:
    """Load the full record of a task."""
    r = get_memory().task_get(task_id)
    return _ok(r) if r.get("ok") else fail(r["error"], category="client_error")


@tool("memory_task_list", category="memory", schema=TaskListIn, timeout_s=3.0)
def memory_task_list(limit: int = 20) -> dict:
    """List recent tasks newest-first."""
    return _ok({"tasks": get_memory().task_list(limit=limit)})


# ---- traces ------------------------------------------------------------------
@tool("memory_trace_recent", category="memory", schema=TraceRecentIn, timeout_s=3.0)
def memory_trace_recent(limit: int = 20, tool: str = "") -> dict:
    """
    Read the most recent action traces (auto-recorded by the server on
    every tool call). Pass tool='shell_cmd' to filter to one tool.
    """
    return _ok({"traces": get_memory().trace_recent(limit=limit, tool=tool or None)})


@tool("memory_trace_failures", category="memory", schema=TraceRecentIn, timeout_s=3.0)
def memory_trace_failures(limit: int = 20, tool: str = "") -> dict:
    """Read recent failed actions to diagnose what's been going wrong."""
    return _ok({"failures": get_memory().trace_failures(tool=tool or None, limit=limit)})


# ---- lessons (self-learning) ------------------------------------------------
@tool("memory_lesson_set", category="memory", schema=LessonSetIn, timeout_s=3.0)
async def memory_lesson_set(name: str, body: str) -> dict:
    """
    Manually record a lesson the model should remember
    (e.g. "When window_focus fails, call window_list first").
    """
    return _ok(await get_memory().lesson_set(name, body, source="manual"))


@tool("memory_lesson_list", category="memory", schema=NoneIn, timeout_s=3.0)
def memory_lesson_list() -> dict:
    """List learned lessons (auto + manual)."""
    return _ok({"lessons": get_memory().lesson_list()})


@tool("memory_lesson_get", category="memory", schema=LessonNameIn, timeout_s=3.0)
def memory_lesson_get(name: str) -> dict:
    """Read the full body of a lesson."""
    r = get_memory().lesson_get(name)
    if r is None:
        return fail(f"no lesson named {name!r}", category="client_error")
    return _ok({"name": name, **r})


@tool("memory_lesson_delete", category="memory", schema=LessonNameIn, timeout_s=3.0)
async def memory_lesson_delete(name: str) -> dict:
    """Delete a lesson."""
    r = await get_memory().lesson_delete(name)
    return _ok(r) if r.get("ok") else fail(r["error"], category="client_error")


@tool("memory_learn_from_traces", category="memory", schema=LearnIn, timeout_s=60.0)
async def memory_learn_from_traces(
    window: int = 200,
    use_lms: bool = False,
    lms_base: str = "http://localhost:1234/v1",
) -> dict:
    """
    Distill recent traces into auto-lessons. Heuristic-only by default.
    Set use_lms=True to ask the loaded model to write the lesson text
    (requires LM Studio or Jan.ai running with a model loaded).
    """
    # Pull the live model ID from the server's runtime state if available.
    host_model_id: str | None = None
    try:
        from server_v2 import _LMS_INFO  # type: ignore[import]
        if isinstance(_LMS_INFO, dict):
            host_model_id = _LMS_INFO.get("model_id")
    except Exception:
        pass  # server module not importable in tests — fallback to None

    return _ok(
        await get_memory().learn_from_traces(
            window=window,
            lms_base=lms_base if use_lms else None,
            host_model_id=host_model_id,
        )
    )


# ---- notes (chunked free-form) ----------------------------------------------
@tool("memory_note_save", category="memory", schema=NoteSaveIn, timeout_s=10.0)
async def memory_note_save(label: str, text: str) -> dict:
    """
    Save large free-form text under a label. Stored chunked on disk so the
    model can load it back one piece at a time without blowing context.
    """
    return _ok(await get_memory().note_save(label, text))


@tool("memory_note_load", category="memory", schema=NoteLoadIn, timeout_s=5.0)
def memory_note_load(label: str, index: int = 0) -> dict:
    """
    Load one chunk of a saved note. Returns has_more=True if there are
    more chunks; call again with the returned next_index.
    """
    r = get_memory().note_load(label, index=index)
    return _ok(r) if r.get("ok") else fail(r["error"], category="client_error")


@tool("memory_note_list", category="memory", schema=NoneIn, timeout_s=3.0)
def memory_note_list() -> dict:
    """List all saved notes with their chunk counts."""
    return _ok({"notes": get_memory().note_list()})


@tool("memory_note_delete", category="memory", schema=NoteLabelIn, timeout_s=5.0)
def memory_note_delete(label: str) -> dict:
    """Delete a saved note (all chunks)."""
    r = get_memory().note_delete(label)
    return _ok(r) if r.get("ok") else fail(r["error"], category="client_error")


# ---- compaction --------------------------------------------------------------
@tool("memory_compact", category="memory", schema=CompactIn, timeout_s=10.0)
async def memory_compact(target_chars: int = 20_000) -> dict:
    """
    Trim the action trace log: replace older records with an aggregate
    summary stored as a fact, keep the most recent N traces in place.
    Returns kept (records kept), compacted (records removed), summary_key.

    Run this when traces grow large or context feels tight. The server
    also does this automatically in the background when traces exceed
    1,000 lines.
    """
    return _ok(await get_memory().compact(target_chars=target_chars))
