"""
Phantom Memory Manager.

Namespaced stores inside data/memory.json:
  facts  : permanent key-value facts (user prefs, system info, project notes)
  tasks  : task state records (goal, steps, status, last updated)
  chunks : large content split into CHUNK_SIZE pieces by chunker.py
  cache  : ephemeral tool/shell output cache, auto-evicted when > CACHE_MAX entries

All stores persist to disk on every write.
FIX: asyncio.Lock on _persist() prevents memory.json corruption from concurrent writes.
"""

from __future__ import annotations
import json
import difflib
import asyncio
import time
import httpx
from pathlib import Path
from typing import Any

LMS_BASE = "http://localhost:1234/v1"
CACHE_MAX = 100
SUMMARY_CHUNK = 5000
SUMMARY_TOKENS = 350


class MemoryManager:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.data_dir.mkdir(exist_ok=True)
        self._path = data_dir / "memory.json"
        self._lock = asyncio.Lock()   # FIX: guards all writes to prevent JSON corruption
        self._db: dict[str, dict] = self._load()
        for ns in ("facts", "tasks", "chunks", "cache"):
            self._db.setdefault(ns, {})

    # ------------------------------------------------------------------
    # Internal persistence
    # ------------------------------------------------------------------

    def _load(self) -> dict:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                if data and not any(k in data for k in ("facts", "tasks", "chunks", "cache")):
                    return {"facts": data, "tasks": {}, "chunks": {}, "cache": {}}
                return data
            except Exception:
                return {}
        return {}

    def _persist(self):
        """Write DB to disk. Must be called from within self._lock."""
        self._path.write_text(
            json.dumps(self._db, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    async def _persist_async(self):
        """Acquire lock then persist. Use for all async write paths."""
        async with self._lock:
            self._persist()

    def _persist_sync(self):
        """
        Sync persist used by synchronous methods (save, delete, cache_set, etc.).
        Since asyncio is single-threaded and sync tool calls don't yield,
        this is safe as long as we never call it from a thread pool.
        """
        self._persist()

    # ------------------------------------------------------------------
    # Raw access (used by chunker.py)
    # ------------------------------------------------------------------

    def raw_set(self, key: str, value: Any):
        self._db["chunks"][key] = value
        self._persist_sync()

    def raw_get(self, key: str) -> Any:
        return self._db["chunks"].get(key)

    def raw_delete(self, key: str) -> bool:
        if key in self._db["chunks"]:
            del self._db["chunks"][key]
            self._persist_sync()
            return True
        return False

    def raw_keys(self) -> list[str]:
        return list(self._db["chunks"].keys())

    # ------------------------------------------------------------------
    # FACTS namespace
    # ------------------------------------------------------------------

    def save(self, key: str, value: str) -> str:
        self._db["facts"][key] = {"value": value, "updated": time.time()}
        self._persist_sync()
        return f"Memory saved: [{key}]"

    def get(self, key: str) -> str:
        entry = self._db["facts"].get(key)
        if entry is None:
            return f"No memory found for key: '{key}'"
        return entry["value"] if isinstance(entry, dict) else str(entry)

    def delete(self, key: str) -> str:
        if key in self._db["facts"]:
            del self._db["facts"][key]
            self._persist_sync()
            return f"Memory deleted: [{key}]"
        return f"Key not found: '{key}'"

    def list_keys(self) -> list[str]:
        return sorted(self._db["facts"].keys())

    def search(self, query: str) -> list[dict]:
        results = []
        q = query.lower()

        for k, entry in self._db["facts"].items():
            v = entry["value"] if isinstance(entry, dict) else str(entry)
            combined = f"{k} {v}".lower()
            score = 1.0 if q in combined else difflib.SequenceMatcher(None, q, combined).ratio()
            if score > 0.28:
                results.append({"namespace": "facts", "key": k, "preview": v[:300], "score": round(score, 2)})

        for task_id, task in self._db["tasks"].items():
            combined = f"{task_id} {task.get('goal','')} {task.get('summary','')}".lower()
            score = 1.0 if q in combined else difflib.SequenceMatcher(None, q, combined).ratio()
            if score > 0.28:
                results.append({
                    "namespace": "tasks", "key": task_id,
                    "preview": task.get("goal", "")[:200],
                    "score": round(score, 2),
                    "status": task.get("status", "unknown"),
                })

        from memory.chunker import list_chunk_labels
        for label in list_chunk_labels(self):
            if q in label.lower():
                results.append({"namespace": "chunks", "key": label, "preview": f"Chunked content ({label})", "score": 0.5})

        return sorted(results, key=lambda x: x["score"], reverse=True)[:15]

    # ------------------------------------------------------------------
    # TASKS namespace
    # ------------------------------------------------------------------

    def task_start(self, task_id: str, goal: str) -> dict:
        self._db["tasks"][task_id] = {
            "goal": goal, "status": "in_progress",
            "steps": [], "summary": "",
            "created": time.time(), "updated": time.time(),
        }
        self._persist_sync()
        return {"ok": True, "task_id": task_id, "message": f"Task '{task_id}' started."}

    def task_update(self, task_id: str, step: str, status: str = "in_progress", summary: str = "") -> dict:
        task = self._db["tasks"].get(task_id)
        if task is None:
            task = {"goal": task_id, "status": "in_progress", "steps": [], "summary": "", "created": time.time()}
            self._db["tasks"][task_id] = task

        task["steps"].append({"ts": time.time(), "step": step})
        task["status"] = status
        if summary:
            task["summary"] = summary
        task["updated"] = time.time()

        if len(task["steps"]) > 50:
            task["steps"] = task["steps"][-50:]

        self._persist_sync()
        return {"ok": True, "task_id": task_id, "status": status, "steps_logged": len(task["steps"])}

    def task_load(self, task_id: str) -> dict:
        task = self._db["tasks"].get(task_id)
        if task is None:
            return {"error": f"Task not found: '{task_id}'"}
        return {"task_id": task_id, **task}

    def task_list(self) -> list[dict]:
        result = []
        for tid, t in self._db["tasks"].items():
            result.append({
                "task_id": tid,
                "goal": t.get("goal", ""),
                "status": t.get("status", "unknown"),
                "step_count": len(t.get("steps", [])),
                "updated": t.get("updated", 0),
            })
        return sorted(result, key=lambda x: x["updated"], reverse=True)

    # ------------------------------------------------------------------
    # CACHE namespace
    # ------------------------------------------------------------------

    def cache_set(self, key: str, value: str, ttl: int = 0) -> str:
        expires = (time.time() + ttl) if ttl > 0 else 0
        self._db["cache"][key] = {"value": value, "stored": time.time(), "expires": expires}
        if len(self._db["cache"]) > CACHE_MAX:
            oldest = min(self._db["cache"], key=lambda k: self._db["cache"][k]["stored"])
            del self._db["cache"][oldest]
        self._persist_sync()
        return f"Cache set: [{key}]"

    def cache_get(self, key: str) -> str:
        entry = self._db["cache"].get(key)
        if entry is None:
            return f"[CACHE MISS] No cache entry for: '{key}'"
        expires = entry.get("expires", 0)
        if expires and time.time() > expires:
            del self._db["cache"][key]
            self._persist_sync()
            return f"[CACHE EXPIRED] Entry '{key}' has expired."
        return entry["value"]

    def cache_list(self) -> list[dict]:
        now = time.time()
        result = []
        for k, v in self._db["cache"].items():
            expires = v.get("expires", 0)
            if expires and now > expires:
                continue
            result.append({"key": k, "stored": v["stored"], "expires": expires or "never", "size_chars": len(v.get("value", ""))})
        return sorted(result, key=lambda x: x["stored"], reverse=True)

    # ------------------------------------------------------------------
    # CHUNK tools
    # ------------------------------------------------------------------

    def chunk_save(self, label: str, text: str) -> dict:
        from memory.chunker import split_and_store
        manifest = split_and_store(text, label, self)
        return {
            "ok": True, "label": label,
            "chunks": manifest["chunk_count"],
            "total_chars": manifest["total_chars"],
            "chunk_size": manifest["chunk_size"],
            "message": (
                f"Saved {manifest['chunk_count']} chunks for '{label}'. "
                f"Load with memory_chunk_load(label='{label}', index=0..{manifest['chunk_count']-1})."
            ),
        }

    def chunk_load(self, label: str, index: int) -> dict:
        from memory.chunker import load_chunk, get_manifest
        manifest = get_manifest(label, self)
        if manifest is None:
            return {"error": f"No chunks found for label: '{label}'"}
        total = manifest["chunk_count"]
        if index < 0 or index >= total:
            return {"error": f"Index {index} out of range (0..{total-1}) for label '{label}'"}
        content = load_chunk(label, index, self)
        return {
            "label": label, "index": index, "total_chunks": total,
            "content": content,
            "has_more": index < total - 1,
            "next_index": index + 1 if index < total - 1 else None,
        }

    def chunk_reassemble(self, label: str) -> dict:
        from memory.chunker import reassemble, get_manifest
        manifest = get_manifest(label, self)
        if manifest is None:
            return {"error": f"No chunks found for label: '{label}'"}
        text = reassemble(label, self)
        return {
            "label": label, "total_chars": len(text),
            "chunks_merged": manifest["chunk_count"],
            "content": text,
            "warning": "LARGE CONTENT" if len(text) > 20000 else None,
        }

    def chunk_list(self) -> list[dict]:
        from memory.chunker import list_chunk_labels, get_manifest
        result = []
        for label in list_chunk_labels(self):
            m = get_manifest(label, self)
            if m:
                result.append({"label": label, "chunks": m["chunk_count"], "total_chars": m["total_chars"]})
        return result

    def chunk_delete(self, label: str) -> dict:
        from memory.chunker import delete_chunks
        deleted = delete_chunks(label, self)
        return {"ok": True, "label": label, "entries_deleted": deleted}

    # ------------------------------------------------------------------
    # COMPRESSION
    # ------------------------------------------------------------------

    async def compress(self, conversation: str, label: str) -> str:
        pieces = [conversation[i:i + SUMMARY_CHUNK] for i in range(0, len(conversation), SUMMARY_CHUNK)]
        summaries = []
        for idx, piece in enumerate(pieces):
            summary = await self._summarize_piece(piece, idx + 1, len(pieces))
            summaries.append(summary)

        final = summaries[0] if len(summaries) == 1 else await self._merge_summaries(
            "\n\n---\n\n".join(summaries), label
        )

        async with self._lock:
            self._db["facts"][f"compressed:{label}"] = {
                "value": final,
                "updated": time.time(),
                "source_chars": len(conversation),
                "source_chunks": len(pieces),
            }
            self._persist()

        return (
            f"Compressed '{label}' into memory ({len(pieces)} chunk(s) summarized). "
            f"Summary preview: {final[:300]}..."
        )

    async def _summarize_piece(self, text: str, part: int, total: int) -> str:
        prompt = (
            f"Summarize part {part} of {total} of this conversation into a compact, "
            f"factual memory digest (max 200 words). Keep decisions, facts, code references, "
            f"file paths, and key context. Remove filler.\n\nTEXT:\n{text}"
        )
        return await self._call_lms(prompt, SUMMARY_TOKENS)

    async def _merge_summaries(self, summaries_text: str, label: str) -> str:
        prompt = (
            f"Merge these {label} conversation summaries into one compact memory digest "
            f"under 300 words. Keep all unique facts, decisions, and context. "
            f"Remove duplicates.\n\nSUMMARIES:\n{summaries_text[:8000]}"
        )
        return await self._call_lms(prompt, 450)

    async def _call_lms(self, prompt: str, max_tokens: int) -> str:
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                resp = await client.post(
                    f"{LMS_BASE}/chat/completions",
                    json={
                        "model": "local-model",
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": max_tokens,
                        "temperature": 0.2,
                    },
                )
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()
        except Exception:
            return f"[LMS UNREACHABLE - raw excerpt] {prompt[200:700]}"
