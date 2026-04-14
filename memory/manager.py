"""
Phantom Memory Manager — full overhaul.

Namespaced stores inside data/memory.json:
  facts     : permanent key-value facts (user prefs, system info, project notes)
  tasks     : task state records (goal, steps, status, last updated)
  chunks    : large content split into CHUNK_SIZE pieces by chunker.py
  cache     : ephemeral tool/shell output cache, auto-evicted when > CACHE_MAX entries

All stores persist to disk on every write.
The manager exposes raw_* methods used internally by chunker.py.
Public methods are called by server.py via the memory_* tools.

LM Studio compression:
  memory_compress splits the conversation into safe chunks first,
  summarizes each chunk individually, then merges summaries into one
  compact memory entry. This avoids the old 6000-char hard cut.
"""

from __future__ import annotations
import json
import difflib
import asyncio
import hashlib
import time
import httpx
from pathlib import Path
from typing import Any

LMS_BASE = "http://localhost:1234/v1"
CACHE_MAX = 100        # max cache entries before oldest is evicted
SUMMARY_CHUNK = 5000   # chars per chunk when compressing conversations
SUMMARY_TOKENS = 350   # max tokens for each per-chunk summary


class MemoryManager:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.data_dir.mkdir(exist_ok=True)
        self._path = data_dir / "memory.json"
        self._db: dict[str, dict] = self._load()
        # Ensure all namespaces exist
        for ns in ("facts", "tasks", "chunks", "cache"):
            self._db.setdefault(ns, {})

    # ------------------------------------------------------------------
    # Internal persistence
    # ------------------------------------------------------------------

    def _load(self) -> dict:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                # Migrate old flat format (pre-overhaul)
                if data and not any(k in data for k in ("facts", "tasks", "chunks", "cache")):
                    return {"facts": data, "tasks": {}, "chunks": {}, "cache": {}}
                return data
            except Exception:
                return {}
        return {}

    def _persist(self):
        self._path.write_text(
            json.dumps(self._db, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # Raw access (used by chunker.py — bypasses namespace logic)
    # ------------------------------------------------------------------

    def raw_set(self, key: str, value: Any):
        self._db["chunks"][key] = value
        self._persist()

    def raw_get(self, key: str) -> Any:
        return self._db["chunks"].get(key)

    def raw_delete(self, key: str) -> bool:
        if key in self._db["chunks"]:
            del self._db["chunks"][key]
            self._persist()
            return True
        return False

    def raw_keys(self) -> list[str]:
        return list(self._db["chunks"].keys())

    # ------------------------------------------------------------------
    # FACTS namespace — permanent named memories
    # ------------------------------------------------------------------

    def save(self, key: str, value: str) -> str:
        """Save a fact. Overwrites existing value."""
        self._db["facts"][key] = {
            "value": value,
            "updated": time.time(),
        }
        self._persist()
        return f"Memory saved: [{key}]"

    def get(self, key: str) -> str:
        entry = self._db["facts"].get(key)
        if entry is None:
            return f"No memory found for key: '{key}'"
        return entry["value"] if isinstance(entry, dict) else str(entry)

    def delete(self, key: str) -> str:
        if key in self._db["facts"]:
            del self._db["facts"][key]
            self._persist()
            return f"Memory deleted: [{key}]"
        return f"Key not found: '{key}'"

    def list_keys(self) -> list[str]:
        return sorted(self._db["facts"].keys())

    def search(self, query: str) -> list[dict]:
        """Fuzzy search across facts and task summaries."""
        results = []
        q = query.lower()

        # Search facts
        for k, entry in self._db["facts"].items():
            v = entry["value"] if isinstance(entry, dict) else str(entry)
            combined = f"{k} {v}".lower()
            if q in combined:
                score = 1.0
            else:
                score = difflib.SequenceMatcher(None, q, combined).ratio()
            if score > 0.28:
                results.append({
                    "namespace": "facts",
                    "key": k,
                    "preview": v[:300],
                    "score": round(score, 2),
                })

        # Search task summaries
        for task_id, task in self._db["tasks"].items():
            combined = f"{task_id} {task.get('goal','')} {task.get('summary','')}".lower()
            if q in combined:
                score = 1.0
            else:
                score = difflib.SequenceMatcher(None, q, combined).ratio()
            if score > 0.28:
                results.append({
                    "namespace": "tasks",
                    "key": task_id,
                    "preview": task.get("goal", "")[:200],
                    "score": round(score, 2),
                    "status": task.get("status", "unknown"),
                })

        # Search chunk labels
        from memory.chunker import list_chunk_labels
        for label in list_chunk_labels(self):
            if q in label.lower():
                results.append({
                    "namespace": "chunks",
                    "key": label,
                    "preview": f"Chunked content ({label})",
                    "score": 0.5,
                })

        return sorted(results, key=lambda x: x["score"], reverse=True)[:15]

    # ------------------------------------------------------------------
    # TASKS namespace — long-running work state
    # ------------------------------------------------------------------

    def task_start(self, task_id: str, goal: str) -> dict:
        """Create or reset a task record."""
        self._db["tasks"][task_id] = {
            "goal": goal,
            "status": "in_progress",
            "steps": [],
            "summary": "",
            "created": time.time(),
            "updated": time.time(),
        }
        self._persist()
        return {"ok": True, "task_id": task_id, "message": f"Task '{task_id}' started."}

    def task_update(self, task_id: str, step: str, status: str = "in_progress", summary: str = "") -> dict:
        """
        Append a step log entry to a task and update its status.
        status: 'in_progress' | 'complete' | 'blocked' | 'failed'
        """
        task = self._db["tasks"].get(task_id)
        if task is None:
            # Auto-create if doesn't exist
            task = {
                "goal": task_id,
                "status": "in_progress",
                "steps": [],
                "summary": "",
                "created": time.time(),
            }
            self._db["tasks"][task_id] = task

        task["steps"].append({"ts": time.time(), "step": step})
        task["status"] = status
        if summary:
            task["summary"] = summary
        task["updated"] = time.time()

        # Keep step log from growing without bound (keep last 50)
        if len(task["steps"]) > 50:
            task["steps"] = task["steps"][-50:]

        self._persist()
        return {
            "ok": True,
            "task_id": task_id,
            "status": status,
            "steps_logged": len(task["steps"]),
        }

    def task_load(self, task_id: str) -> dict:
        """Load a full task record including all steps."""
        task = self._db["tasks"].get(task_id)
        if task is None:
            return {"error": f"Task not found: '{task_id}'"}
        return {"task_id": task_id, **task}

    def task_list(self) -> list[dict]:
        """List all tasks with their current status (no step log)."""
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
    # CACHE namespace — ephemeral tool output / scratch space
    # ------------------------------------------------------------------

    def cache_set(self, key: str, value: str, ttl: int = 0) -> str:
        """
        Store a value in the ephemeral cache.
        ttl: seconds until expiry. 0 = no expiry.
        Evicts oldest entry if CACHE_MAX is exceeded.
        """
        expires = (time.time() + ttl) if ttl > 0 else 0
        self._db["cache"][key] = {
            "value": value,
            "stored": time.time(),
            "expires": expires,
        }
        # Evict oldest if over limit
        if len(self._db["cache"]) > CACHE_MAX:
            oldest = min(self._db["cache"], key=lambda k: self._db["cache"][k]["stored"])
            del self._db["cache"][oldest]
        self._persist()
        return f"Cache set: [{key}]"

    def cache_get(self, key: str) -> str:
        """Retrieve a cache value. Returns error string if missing or expired."""
        entry = self._db["cache"].get(key)
        if entry is None:
            return f"[CACHE MISS] No cache entry for: '{key}'"
        expires = entry.get("expires", 0)
        if expires and time.time() > expires:
            del self._db["cache"][key]
            self._persist()
            return f"[CACHE EXPIRED] Entry '{key}' has expired."
        return entry["value"]

    def cache_list(self) -> list[dict]:
        """List all non-expired cache keys."""
        now = time.time()
        result = []
        for k, v in self._db["cache"].items():
            expires = v.get("expires", 0)
            if expires and now > expires:
                continue
            result.append({
                "key": k,
                "stored": v["stored"],
                "expires": expires or "never",
                "size_chars": len(v.get("value", "")),
            })
        return sorted(result, key=lambda x: x["stored"], reverse=True)

    # ------------------------------------------------------------------
    # CHUNK tools (delegates to chunker.py)
    # ------------------------------------------------------------------

    def chunk_save(self, label: str, text: str) -> dict:
        """Split large text and save as numbered chunks."""
        from memory.chunker import split_and_store
        manifest = split_and_store(text, label, self)
        return {
            "ok": True,
            "label": label,
            "chunks": manifest["chunk_count"],
            "total_chars": manifest["total_chars"],
            "chunk_size": manifest["chunk_size"],
            "message": (
                f"Saved {manifest['chunk_count']} chunks for '{label}'. "
                f"Load with memory_chunk_load(label='{label}', index=0..{manifest['chunk_count']-1}) "
                f"or memory_chunk_reassemble(label='{label}') to get everything at once."
            ),
        }

    def chunk_load(self, label: str, index: int) -> dict:
        """Load a single chunk by label and index."""
        from memory.chunker import load_chunk, get_manifest
        manifest = get_manifest(label, self)
        if manifest is None:
            return {"error": f"No chunks found for label: '{label}'"}
        total = manifest["chunk_count"]
        if index < 0 or index >= total:
            return {"error": f"Index {index} out of range (0..{total-1}) for label '{label}'"}
        content = load_chunk(label, index, self)
        return {
            "label": label,
            "index": index,
            "total_chunks": total,
            "content": content,
            "has_more": index < total - 1,
            "next_index": index + 1 if index < total - 1 else None,
        }

    def chunk_reassemble(self, label: str) -> dict:
        """Reassemble all chunks into full text. Use only if full text fits in context."""
        from memory.chunker import reassemble, get_manifest
        manifest = get_manifest(label, self)
        if manifest is None:
            return {"error": f"No chunks found for label: '{label}'"}
        text = reassemble(label, self)
        return {
            "label": label,
            "total_chars": len(text),
            "chunks_merged": manifest["chunk_count"],
            "content": text,
            "warning": (
                "LARGE CONTENT" if len(text) > 20000
                else None
            ),
        }

    def chunk_list(self) -> list[dict]:
        """List all chunk labels and their manifest info."""
        from memory.chunker import list_chunk_labels, get_manifest
        result = []
        for label in list_chunk_labels(self):
            m = get_manifest(label, self)
            if m:
                result.append({
                    "label": label,
                    "chunks": m["chunk_count"],
                    "total_chars": m["total_chars"],
                })
        return result

    def chunk_delete(self, label: str) -> dict:
        from memory.chunker import delete_chunks
        deleted = delete_chunks(label, self)
        return {"ok": True, "label": label, "entries_deleted": deleted}

    # ------------------------------------------------------------------
    # COMPRESSION — intelligent chunked summarization
    # ------------------------------------------------------------------

    async def compress(self, conversation: str, label: str) -> str:
        """
        Compress a long conversation into a compact memory fact.
        Splits into SUMMARY_CHUNK pieces, summarizes each via LM Studio,
        then merges summaries into a single digest. Stored as facts:<compressed:label>.
        Falls back gracefully if LM Studio is unreachable.
        """
        # Split into manageable pieces
        pieces = [
            conversation[i:i + SUMMARY_CHUNK]
            for i in range(0, len(conversation), SUMMARY_CHUNK)
        ]

        summaries = []
        for idx, piece in enumerate(pieces):
            summary = await self._summarize_piece(piece, idx + 1, len(pieces))
            summaries.append(summary)

        if len(summaries) == 1:
            final = summaries[0]
        else:
            merged_input = "\n\n---\n\n".join(summaries)
            final = await self._merge_summaries(merged_input, label)

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
        except Exception as e:
            return f"[LMS UNREACHABLE — raw excerpt] {prompt[200:700]}"
