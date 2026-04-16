"""
phantom.tools.memory — the model's durable scratchpad.

Six tools, one adapter. Deliberately small surface:

    memory_save      store a key → value
    memory_get       recall a value by key
    memory_delete    forget a key
    memory_list      list stored keys (optionally by prefix)
    memory_search    BM25 ranked search over facts + task summaries
    task_start       begin a multi-step task record
    task_update      append a step, update status/summary
    task_load        read a task back
    task_list        list tasks (optionally filter by status)

What we deliberately do NOT expose:
  * chunk_save / chunk_load / chunk_reassemble / chunk_list / chunk_delete
    — chunking is an implementation detail for large values. The adapter
    handles it transparently inside memory_save / memory_get. Every tool
    we remove is context we give back to the model.
  * cache_set / cache_get — internal to the planner; not an agent tool.
  * compress — triggered by the planner when the conversation gets long;
    not a tool the model calls directly.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from phantom.contracts import fail, ok
from phantom.memory import get_memory
from phantom.tools._base import tool


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------


class MemorySaveInput(BaseModel):
    key: str = Field(..., min_length=1, max_length=200, description="Stable identifier, e.g. 'user.timezone'.")
    value: str = Field(..., description="String payload. Serialize structured data before calling.")

    model_config = ConfigDict(extra="forbid")


class MemoryGetInput(BaseModel):
    key: str = Field(..., min_length=1, max_length=200)
    model_config = ConfigDict(extra="forbid")


class MemoryDeleteInput(BaseModel):
    key: str = Field(..., min_length=1, max_length=200)
    model_config = ConfigDict(extra="forbid")


class MemoryListInput(BaseModel):
    prefix: str = Field("", description="Only return keys starting with this prefix.")
    model_config = ConfigDict(extra="forbid")


class MemorySearchInput(BaseModel):
    query: str = Field(..., min_length=1)
    limit: int = Field(10, ge=1, le=50)
    model_config = ConfigDict(extra="forbid")


@tool("memory_save", category="memory", schema=MemorySaveInput, timeout_s=5.0)
def memory_save(key: str, value: str) -> dict:
    """
    Store `value` under `key`. Overwrites any existing value for that key.

    Large values (>6000 chars) are chunked transparently under the hood;
    you still retrieve them with a single memory_get call.

    Use for: user preferences, facts that should survive across turns,
    long-running task notes. NOT for transient reasoning — that belongs
    in the conversation, not durable memory.
    """
    return ok(get_memory().save(key, value))


@tool("memory_get", category="memory", schema=MemoryGetInput, timeout_s=5.0)
def memory_get(key: str) -> dict:
    """
    Retrieve a value previously saved with memory_save. If the key doesn't
    exist, returns an envelope with ok=false and reason='not_found'.
    """
    result = get_memory().get(key)
    if not result["ok"]:
        return fail(
            f"No memory found for key {key!r}.",
            hint="Use memory_list to see stored keys, or memory_search for ranked matches.",
            category="client_error",
            key=key,
        )
    return ok(result)


@tool("memory_delete", category="memory", schema=MemoryDeleteInput, timeout_s=5.0)
def memory_delete(key: str) -> dict:
    """Forget a key. Idempotent — deleting a non-existent key is not an error."""
    return ok(get_memory().delete(key))


@tool("memory_list", category="memory", schema=MemoryListInput, timeout_s=5.0)
def memory_list(prefix: str = "") -> dict:
    """
    List all keys currently in memory.

    Pass a `prefix` to narrow results, e.g. prefix='user.' returns only
    user.* keys. Returns just keys, not values — use memory_get to read.
    """
    keys = get_memory().list_keys(prefix=prefix)
    return ok({"keys": keys, "count": len(keys), "prefix": prefix})


@tool("memory_search", category="memory", schema=MemorySearchInput, timeout_s=10.0)
def memory_search(query: str, limit: int = 10) -> dict:
    """
    BM25-ranked search over saved facts and task summaries.

    Use when you don't know the exact key but remember some content.
    Returns ranked hits with namespace, key, preview, and score.
    """
    hits = get_memory().search(query, limit=limit)
    return ok({"query": query, "hits": hits, "count": len(hits)})


# ---------------------------------------------------------------------------
# Tasks (state machine for multi-step work)
# ---------------------------------------------------------------------------


TaskStatus = Literal["in_progress", "blocked", "done", "failed"]


class TaskStartInput(BaseModel):
    task_id: str = Field(..., min_length=1, max_length=120, description="Short stable id, e.g. 'refactor-pr3'.")
    goal: str = Field(..., min_length=1, description="One-sentence statement of what done looks like.")
    model_config = ConfigDict(extra="forbid")


class TaskUpdateInput(BaseModel):
    task_id: str = Field(..., min_length=1, max_length=120)
    step: str = Field(..., min_length=1, description="What just happened, or what is about to happen next.")
    status: TaskStatus = Field("in_progress")
    summary: str = Field("", description="Optional running summary; overwrites prior summary when set.")
    model_config = ConfigDict(extra="forbid")


class TaskLoadInput(BaseModel):
    task_id: str = Field(..., min_length=1, max_length=120)
    model_config = ConfigDict(extra="forbid")


class TaskListInput(BaseModel):
    status: TaskStatus | None = Field(None, description="Filter to tasks with this status. Omit for all.")
    model_config = ConfigDict(extra="forbid")


@tool("task_start", category="memory", schema=TaskStartInput, timeout_s=5.0)
def task_start(task_id: str, goal: str) -> dict:
    """
    Begin a durable task record. Use at the start of any multi-step job
    (research, refactor, deployment) so the planner can resume after a
    crash or reload. Pair with task_update on every meaningful step.
    """
    return ok(get_memory().task_start(task_id, goal))


@tool("task_update", category="memory", schema=TaskUpdateInput, timeout_s=5.0)
def task_update(task_id: str, step: str, status: str = "in_progress", summary: str = "") -> dict:
    """
    Append a step to a task's history and optionally update its status.

    Valid statuses: in_progress, blocked, done, failed. Call this every
    time you take a meaningful action toward the goal. The step list is
    capped at the most recent 50 entries automatically.
    """
    return ok(get_memory().task_update(task_id, step, status=status, summary=summary))


@tool("task_load", category="memory", schema=TaskLoadInput, timeout_s=5.0)
def task_load(task_id: str) -> dict:
    """
    Read back a task record — goal, status, step history, summary. Use to
    resume where you left off or to build a progress report.
    """
    result = get_memory().task_load(task_id)
    if isinstance(result, dict) and result.get("error"):
        return fail(result["error"], hint="Call task_list to see known task ids.", category="client_error")
    return ok(result)


@tool("task_list", category="memory", schema=TaskListInput, timeout_s=5.0)
def task_list(status: str | None = None) -> dict:
    """
    List known tasks, most recently updated first. Pass status='in_progress'
    or 'blocked' to filter the output.
    """
    tasks = get_memory().task_list(status=status)
    return ok({"tasks": tasks, "count": len(tasks), "filter_status": status})
