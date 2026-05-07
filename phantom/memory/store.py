"""
PhantomMemory — durable, scoped memory used by the new tool layer.

Public API:
  fact_set(key, value)            -> dict
  fact_get(key)                   -> dict
  fact_search(query, top_k=8)     -> list[dict]   # BM25-like word overlap
  fact_list()                     -> list[str]
  fact_delete(key)                -> bool

  task_start(task_id, goal)       -> dict
  task_step(task_id, step, ok)    -> dict
  task_finish(task_id, status, summary)
  task_get(task_id)               -> dict
  task_list(limit=20)             -> list[dict]

  trace_append(tool, args, ok, error=None, latency_ms=None)
  trace_recent(limit=20, tool=None) -> list[dict]
  trace_failures(tool=None, limit=20) -> list[dict]

  lesson_set(name, body)
  lesson_list() -> list[dict]
  lesson_get(name) -> dict | None

  note_save(label, text)          -> dict     # chunked
  note_load(label, index=0)       -> dict
  note_list()                     -> list[dict]
  note_delete(label)              -> dict

  learn_from_traces(window=200)   -> dict     # distill lessons; LM Studio if reachable
  compact(target_chars=20000)     -> dict     # retire old traces, summarize tasks

The store is process-local and file-backed. Concurrency is async-safe
within a single process via an asyncio.Lock; we don't promise multi-
process write safety because phantom is intended to run as one server.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# Soft cap on a single trace record's size — keep traces grep-friendly.
TRACE_ARGS_PREVIEW = 280
TRACE_ERROR_PREVIEW = 400
NOTE_CHUNK_CHARS = 6000
TRACE_KEEP_AFTER_COMPACT = 100


@dataclass
class MemoryConfig:
    data_dir: Path
    note_chunk_chars: int = NOTE_CHUNK_CHARS
    trace_keep_after_compact: int = TRACE_KEEP_AFTER_COMPACT


class PhantomMemory:
    def __init__(self, data_dir: str | Path, *, config: MemoryConfig | None = None):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "notes").mkdir(exist_ok=True)
        self.config = config or MemoryConfig(data_dir=self.dir)
        self._lock = asyncio.Lock()
        # In-memory caches (re-read on each instance creation, written through).
        self._facts: dict[str, dict] = self._read_json("facts.json", {})
        self._tasks: dict[str, dict] = self._read_json("tasks.json", {})
        self._lessons: dict[str, dict] = self._read_json("lessons.json", {})

    # ------------------------------------------------------------------
    # Disk helpers
    # ------------------------------------------------------------------
    def _read_json(self, name: str, default):
        p = self.dir / name
        if not p.exists():
            return default
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return default

    def _write_json(self, name: str, payload):
        p = self.dir / name
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(p)

    # ------------------------------------------------------------------
    # FACTS
    # ------------------------------------------------------------------
    def fact_set(self, key: str, value: str) -> dict:
        self._facts[key] = {"value": value, "updated": time.time()}
        self._write_json("facts.json", self._facts)
        return {"ok": True, "key": key, "chars": len(value)}

    def fact_get(self, key: str) -> dict:
        entry = self._facts.get(key)
        if entry is None:
            return {"ok": False, "error": f"no fact named {key!r}"}
        return {"ok": True, "key": key, "value": entry["value"], "updated": entry.get("updated")}

    def fact_delete(self, key: str) -> dict:
        if key in self._facts:
            del self._facts[key]
            self._write_json("facts.json", self._facts)
            return {"ok": True, "deleted": key}
        return {"ok": False, "error": f"no fact named {key!r}"}

    def fact_list(self) -> list[str]:
        return sorted(self._facts.keys())

    def fact_search(self, query: str, top_k: int = 8) -> list[dict]:
        """Simple word-overlap retrieval — cheap, deterministic, no model needed."""
        q_terms = _tokenize(query)
        if not q_terms:
            return []
        scored: list[tuple[float, str, str]] = []
        for k, entry in self._facts.items():
            v = entry.get("value", "") if isinstance(entry, dict) else str(entry)
            terms = set(_tokenize(f"{k} {v}"))
            if not terms:
                continue
            overlap = len(q_terms & terms)
            if overlap == 0:
                continue
            score = overlap / (len(q_terms) + len(terms) - overlap)
            scored.append((score, k, v))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {"key": k, "score": round(s, 3), "preview": v[:300]}
            for s, k, v in scored[:top_k]
        ]

    # ------------------------------------------------------------------
    # TASKS
    # ------------------------------------------------------------------
    def task_start(self, task_id: str, goal: str) -> dict:
        self._tasks[task_id] = {
            "goal": goal,
            "status": "in_progress",
            "steps": [],
            "summary": "",
            "created": time.time(),
            "updated": time.time(),
        }
        self._write_json("tasks.json", self._tasks)
        return {"ok": True, "task_id": task_id}

    def task_step(self, task_id: str, step: str, ok: bool = True, detail: str = "") -> dict:
        task = self._tasks.get(task_id) or {
            "goal": task_id, "status": "in_progress", "steps": [],
            "summary": "", "created": time.time(),
        }
        task["steps"].append({
            "ts": time.time(),
            "step": step,
            "ok": bool(ok),
            "detail": detail[:TRACE_ARGS_PREVIEW] if detail else "",
        })
        if len(task["steps"]) > 100:
            task["steps"] = task["steps"][-100:]
        task["updated"] = time.time()
        self._tasks[task_id] = task
        self._write_json("tasks.json", self._tasks)
        return {"ok": True, "task_id": task_id, "step_count": len(task["steps"])}

    def task_finish(self, task_id: str, status: str = "done", summary: str = "") -> dict:
        task = self._tasks.get(task_id)
        if task is None:
            return {"ok": False, "error": f"unknown task {task_id!r}"}
        task["status"] = status
        if summary:
            task["summary"] = summary
        task["updated"] = time.time()
        self._write_json("tasks.json", self._tasks)
        return {"ok": True, "task_id": task_id, "status": status}

    def task_get(self, task_id: str) -> dict:
        task = self._tasks.get(task_id)
        if not task:
            return {"ok": False, "error": f"unknown task {task_id!r}"}
        return {"ok": True, "task_id": task_id, **task}

    def task_list(self, limit: int = 20) -> list[dict]:
        items = []
        for tid, t in self._tasks.items():
            items.append({
                "task_id": tid,
                "goal": t.get("goal", "")[:200],
                "status": t.get("status", "unknown"),
                "step_count": len(t.get("steps", [])),
                "updated": t.get("updated", 0),
            })
        items.sort(key=lambda x: x["updated"], reverse=True)
        return items[:limit]

    # ------------------------------------------------------------------
    # TRACES (action history; basis for self-learning)
    # ------------------------------------------------------------------
    def trace_append(
        self,
        tool: str,
        args: dict | None,
        ok: bool,
        *,
        error: str | None = None,
        latency_ms: int | None = None,
        category: str | None = None,
    ) -> None:
        rec = {
            "ts": time.time(),
            "tool": tool,
            "ok": bool(ok),
            "args": _summarize_args(args, TRACE_ARGS_PREVIEW),
            "error": (error or "")[:TRACE_ERROR_PREVIEW] if not ok else None,
            "category": category,
            "latency_ms": latency_ms,
        }
        with (self.dir / "traces.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def trace_recent(self, limit: int = 20, tool: str | None = None) -> list[dict]:
        traces = list(self._read_traces())
        if tool:
            traces = [t for t in traces if t.get("tool") == tool]
        return traces[-limit:]

    def trace_failures(self, tool: str | None = None, limit: int = 20) -> list[dict]:
        traces = [t for t in self._read_traces() if not t.get("ok")]
        if tool:
            traces = [t for t in traces if t.get("tool") == tool]
        return traces[-limit:]

    def _read_traces(self) -> Iterable[dict]:
        p = self.dir / "traces.jsonl"
        if not p.exists():
            return []
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except Exception:
                continue
        return out

    # ------------------------------------------------------------------
    # LESSONS (distilled action knowledge)
    # ------------------------------------------------------------------
    def lesson_set(self, name: str, body: str, *, source: str = "manual") -> dict:
        self._lessons[name] = {"body": body, "updated": time.time(), "source": source}
        self._write_json("lessons.json", self._lessons)
        return {"ok": True, "name": name}

    def lesson_get(self, name: str) -> dict | None:
        return self._lessons.get(name)

    def lesson_list(self) -> list[dict]:
        return [
            {"name": k, "preview": v.get("body", "")[:200], "source": v.get("source"), "updated": v.get("updated")}
            for k, v in self._lessons.items()
        ]

    def lesson_delete(self, name: str) -> dict:
        if name in self._lessons:
            del self._lessons[name]
            self._write_json("lessons.json", self._lessons)
            return {"ok": True, "deleted": name}
        return {"ok": False, "error": f"no lesson named {name!r}"}

    # ------------------------------------------------------------------
    # NOTES (large free-form text; chunked retrieval)
    # ------------------------------------------------------------------
    def note_save(self, label: str, text: str) -> dict:
        chunks = [
            text[i : i + self.config.note_chunk_chars]
            for i in range(0, len(text), self.config.note_chunk_chars)
        ] or [""]
        meta = {
            "label": label,
            "chunks": len(chunks),
            "total_chars": len(text),
            "chunk_chars": self.config.note_chunk_chars,
            "updated": time.time(),
        }
        out_dir = self.dir / "notes" / _safe_filename(label)
        out_dir.mkdir(parents=True, exist_ok=True)
        for i, c in enumerate(chunks):
            (out_dir / f"chunk_{i:04d}.txt").write_text(c, encoding="utf-8")
        (out_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return {"ok": True, **meta}

    def note_load(self, label: str, index: int = 0) -> dict:
        out_dir = self.dir / "notes" / _safe_filename(label)
        meta_p = out_dir / "meta.json"
        if not meta_p.exists():
            return {"ok": False, "error": f"no note labeled {label!r}"}
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
        total = meta["chunks"]
        if index < 0 or index >= total:
            return {"ok": False, "error": f"index {index} out of range (0..{total-1})"}
        body = (out_dir / f"chunk_{index:04d}.txt").read_text(encoding="utf-8")
        return {
            "ok": True,
            "label": label,
            "index": index,
            "total_chunks": total,
            "has_more": index < total - 1,
            "next_index": index + 1 if index < total - 1 else None,
            "content": body,
        }

    def note_list(self) -> list[dict]:
        out = []
        notes_dir = self.dir / "notes"
        if not notes_dir.exists():
            return []
        for child in sorted(notes_dir.iterdir()):
            meta_p = child / "meta.json"
            if meta_p.exists():
                try:
                    out.append(json.loads(meta_p.read_text(encoding="utf-8")))
                except Exception:
                    continue
        return out

    def note_delete(self, label: str) -> dict:
        import shutil

        out_dir = self.dir / "notes" / _safe_filename(label)
        if not out_dir.exists():
            return {"ok": False, "error": f"no note labeled {label!r}"}
        shutil.rmtree(out_dir)
        return {"ok": True, "deleted": label}

    # ------------------------------------------------------------------
    # SELF-LEARNING + COMPACTION
    # ------------------------------------------------------------------
    async def learn_from_traces(
        self,
        window: int = 200,
        *,
        lms_base: str | None = None,
    ) -> dict:
        """
        Look at recent traces; record one or more lessons for tools that
        repeatedly fail. Heuristic-only by default; if `lms_base` is given,
        try to ask LM Studio for a free-form summary.
        """
        all_traces = list(self._read_traces())
        recent = all_traces[-window:]
        failures: dict[str, list[dict]] = {}
        successes: dict[str, int] = {}
        for t in recent:
            name = t.get("tool", "?")
            if t.get("ok"):
                successes[name] = successes.get(name, 0) + 1
            else:
                failures.setdefault(name, []).append(t)

        learned: list[str] = []
        for tool_name, fails in failures.items():
            if len(fails) < 2:
                continue
            # Extract the most common error category and a short pattern.
            cats = [f.get("category") for f in fails if f.get("category")]
            cat = max(set(cats), key=cats.count) if cats else "unknown"
            sample_err = fails[-1].get("error") or ""
            success_count = successes.get(tool_name, 0)
            ratio = len(fails) / max(1, len(fails) + success_count)
            body = (
                f"Tool '{tool_name}' has been failing in the recent {window} actions "
                f"({len(fails)} failures, {success_count} successes; failure rate "
                f"{ratio:.0%}). Most common category: {cat}. Latest error: "
                f"{sample_err[:200]}. Consider validating arguments before calling, "
                f"or trying a fallback tool."
            )

            if lms_base:
                try:
                    body = await _ask_lms_for_lesson(lms_base, tool_name, fails, success_count)
                except Exception:
                    pass  # fall back to heuristic body

            self.lesson_set(f"auto:{tool_name}", body, source="auto")
            learned.append(tool_name)

        return {
            "ok": True,
            "examined": len(recent),
            "lessons_written": learned,
            "tools_with_failures": list(failures.keys()),
        }

    def compact(self, target_chars: int = 20_000) -> dict:
        """
        Trim trace log to last N records. Records older than that are
        replaced by an aggregate summary appended into facts under
        `trace_summary:<timestamp>` so we never lose the long-term shape.
        """
        traces = list(self._read_traces())
        if len(traces) <= self.config.trace_keep_after_compact:
            return {"ok": True, "trimmed": False, "kept": len(traces)}

        cutoff = len(traces) - self.config.trace_keep_after_compact
        old, kept = traces[:cutoff], traces[cutoff:]
        # Aggregate summary
        per_tool: dict[str, dict] = {}
        for t in old:
            name = t.get("tool", "?")
            entry = per_tool.setdefault(name, {"calls": 0, "errors": 0})
            entry["calls"] += 1
            if not t.get("ok"):
                entry["errors"] += 1
        summary = {
            "compacted_records": len(old),
            "from_ts": old[0]["ts"] if old else None,
            "to_ts": old[-1]["ts"] if old else None,
            "per_tool": per_tool,
        }
        ts = int(time.time())
        self.fact_set(f"trace_summary:{ts}", json.dumps(summary, ensure_ascii=False))
        # Rewrite traces.jsonl with kept-only.
        p = self.dir / "traces.jsonl"
        p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept), encoding="utf-8")
        return {"ok": True, "trimmed": True, "kept": len(kept), "summary_key": f"trace_summary:{ts}"}


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
_WORD_RE = re.compile(r"[A-Za-z0-9_]+")


def _tokenize(text: str) -> set[str]:
    return {w.lower() for w in _WORD_RE.findall(text or "")}


def _summarize_args(args: dict | None, cap: int) -> dict:
    if not args:
        return {}
    out = {}
    for k, v in args.items():
        s = str(v)
        out[k] = s[:cap] + ("…" if len(s) > cap else "")
    return out


def _safe_filename(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", label)[:120] or "_unnamed"


async def _ask_lms_for_lesson(base: str, tool: str, fails: list[dict], successes: int) -> str:
    import httpx

    sample = "\n".join(
        f"- ts={int(f.get('ts',0))} cat={f.get('category')} err={f.get('error','')[:200]}"
        for f in fails[-5:]
    )
    prompt = (
        f"Write one short paragraph of operational guidance (<=80 words) for "
        f"future calls to the tool '{tool}'. It has had {len(fails)} recent failures "
        f"and {successes} successes. Recent failures:\n{sample}\n\n"
        f"Focus on what to check or what fallback to try. No headers, no lists."
    )
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{base}/chat/completions",
            json={
                "model": "local-model",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 220,
                "temperature": 0.2,
            },
        )
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
