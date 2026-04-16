"""
MemoryAdapter — stable API over the legacy memory.manager.MemoryManager.

Public methods (keep this list short; every addition is context the
model has to reason about):

    # Facts
    save(key, value)            -> dict {ok, key, chunked, chunks}
    get(key)                    -> dict {ok, key, value, chunked, chunks}
    delete(key)                 -> dict {ok, key, existed}
    list_keys(prefix="")        -> list[str]
    search(query, limit=10)     -> list[{namespace, key, preview, score, ...}]

    # Tasks (state machine for the planner in PR 4)
    task_start(task_id, goal)
    task_update(task_id, step, status="in_progress", summary="")
    task_load(task_id)
    task_list(status=None)

    # Maintenance (rarely needed; not advertised as tools in PR 2)
    cache_set / cache_get / cache_list
    compress(conversation, label)      async, uses LM Studio

Chunking is handled internally — callers never see chunk manifests.
If `value` exceeds CHUNK_THRESHOLD chars, save() transparently splits
it and get() transparently reassembles it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

# We import the legacy MemoryManager lazily so a bad data/ directory
# doesn't explode at module import time.
_MANAGER: "Any | None" = None

# Values longer than this get chunked. 6000 chars ≈ 1700 tokens, same
# default the legacy chunker uses.
CHUNK_THRESHOLD = 6000


def get_memory(data_dir: Path | None = None) -> "MemoryAdapter":
    """Singleton accessor. `data_dir` is only honored on first call."""
    global _MANAGER
    if _MANAGER is None:
        from memory.manager import MemoryManager

        root = data_dir or (Path(__file__).resolve().parents[2] / "data")
        _MANAGER = MemoryManager(root)
    return MemoryAdapter(_MANAGER)


class MemoryAdapter:
    """
    Wraps a legacy MemoryManager with a thinner, typed surface.

    All methods are sync. The legacy manager exposes some async helpers
    (compress, _persist_async); those are exposed explicitly as async
    methods on this adapter.
    """

    def __init__(self, manager: Any):
        self._m = manager

    # ------------------------------------------------------------------
    # Facts (with transparent chunking)
    # ------------------------------------------------------------------

    def save(self, key: str, value: str) -> dict:
        if not isinstance(key, str) or not key:
            raise ValueError("key must be a non-empty string")
        if not isinstance(value, str):
            raise TypeError("value must be a string (serialize structured data first)")

        if len(value) > CHUNK_THRESHOLD:
            # Transparent chunking: store the bulk in the chunks namespace
            # and keep a small pointer in facts so `get` can find it.
            label = f"auto:{key}"
            manifest = self._m.chunk_save(label=label, text=value)
            pointer = {
                "__chunked__": True,
                "label": label,
                "chunks": manifest["chunks"],
                "total_chars": manifest["total_chars"],
            }
            # Facts values are strings in the legacy store, so serialize.
            import json

            self._m.save(key, json.dumps(pointer))
            return {
                "ok": True,
                "key": key,
                "chunked": True,
                "chunks": manifest["chunks"],
                "total_chars": manifest["total_chars"],
            }

        self._m.save(key, value)
        return {"ok": True, "key": key, "chunked": False, "chunks": 1, "total_chars": len(value)}

    def get(self, key: str) -> dict:
        raw = self._m.get(key)
        if isinstance(raw, str) and raw.startswith("No memory found"):
            return {"ok": False, "key": key, "value": None, "reason": "not_found"}

        # Detect our transparent-chunk pointer.
        import json

        pointer = None
        try:
            maybe = json.loads(raw)
            if isinstance(maybe, dict) and maybe.get("__chunked__"):
                pointer = maybe
        except (json.JSONDecodeError, TypeError):
            pass

        if pointer is not None:
            reassembled = self._m.chunk_reassemble(pointer["label"])
            text = reassembled.get("content", "") if isinstance(reassembled, dict) else ""
            return {
                "ok": True,
                "key": key,
                "value": text,
                "chunked": True,
                "chunks": pointer.get("chunks"),
                "total_chars": pointer.get("total_chars"),
            }

        return {"ok": True, "key": key, "value": raw, "chunked": False}

    def delete(self, key: str) -> dict:
        # If this key pointed at chunks, clean those up first.
        raw = self._m.get(key)
        existed = not (isinstance(raw, str) and raw.startswith("No memory found"))
        if existed:
            import json

            try:
                maybe = json.loads(raw)
                if isinstance(maybe, dict) and maybe.get("__chunked__"):
                    self._m.chunk_delete(maybe["label"])
            except (json.JSONDecodeError, TypeError):
                pass
        msg = self._m.delete(key)
        return {
            "ok": not msg.startswith("Key not found"),
            "key": key,
            "existed": existed,
        }

    def list_keys(self, prefix: str = "") -> list[str]:
        keys = self._m.list_keys()
        if prefix:
            return [k for k in keys if k.startswith(prefix)]
        return list(keys)

    def search(self, query: str, limit: int = 10) -> list[dict]:
        if not query or not query.strip():
            return []
        # Try BM25 first; fall back to the legacy difflib search if
        # rank_bm25 isn't installed.
        try:
            from phantom.memory.bm25 import bm25_search

            hits = bm25_search(self._m, query, limit=limit)
            if hits:
                return hits
        except Exception:
            pass
        return self._m.search(query)[:limit]

    # ------------------------------------------------------------------
    # Tasks (state machine)
    # ------------------------------------------------------------------

    def task_start(self, task_id: str, goal: str) -> dict:
        return self._m.task_start(task_id, goal)

    def task_update(
        self,
        task_id: str,
        step: str,
        status: str = "in_progress",
        summary: str = "",
    ) -> dict:
        valid = {"in_progress", "blocked", "done", "failed"}
        if status not in valid:
            raise ValueError(f"status must be one of {sorted(valid)}, got {status!r}")
        return self._m.task_update(task_id, step, status, summary)

    def task_load(self, task_id: str) -> dict:
        return self._m.task_load(task_id)

    def task_list(self, status: str | None = None) -> list[dict]:
        tasks = self._m.task_list()
        if status is None:
            return tasks
        return [t for t in tasks if t.get("status") == status]

    # ------------------------------------------------------------------
    # Cache passthroughs (not advertised as tools in PR 2)
    # ------------------------------------------------------------------

    def cache_set(self, key: str, value: str, ttl: int = 0) -> dict:
        msg = self._m.cache_set(key, value, ttl)
        return {"ok": not msg.startswith("ERROR"), "key": key}

    def cache_get(self, key: str) -> dict:
        raw = self._m.cache_get(key)
        if raw.startswith("[CACHE MISS]") or raw.startswith("[CACHE EXPIRED]"):
            return {"ok": False, "key": key, "value": None, "reason": raw}
        return {"ok": True, "key": key, "value": raw}

    # ------------------------------------------------------------------
    # Async helpers
    # ------------------------------------------------------------------

    async def compress(self, conversation: str, label: str) -> str:
        return await self._m.compress(conversation, label)
