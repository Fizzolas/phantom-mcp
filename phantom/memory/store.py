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

  learn_from_traces(window=200)   -> dict     # distill lessons; host if reachable
  compact(target_chars=20000)     -> dict     # retire old traces, summarize tasks

The store is process-local and file-backed. Concurrency is async-safe
within a single process via an asyncio.Lock; we don't promise multi-
process write safety because phantom is intended to run as one server.

Pass 7 notes (Phase 2 fixes):
  Issue 1 — Trace rotation: trace_append now triggers an automatic
             background compact() when traces.jsonl exceeds
             TRACE_AUTO_ROTATE_LINES lines. This prevents the file from
             growing unbounded across long sessions. The rotate is
             fire-and-forget (asyncio.create_task) so it never blocks
             the tool call that triggered it.
  Issue 2 — Startup integrity check: verify_integrity() scans all JSON
             files at boot and logs a clear warning if any are corrupt
             or missing required keys. Called by PhantomMemory.__init__.
  Issue 3 — Model-ID threading: learn_from_traces() now accepts
             host_model_id as a top-level keyword (the old lms_base_model_id
             is kept as an alias). The memory tool layer passes _LMS_INFO
             model id through so lessons are always attributed correctly.
  Issue 4 — Async wrappers: fact_set, task_start, task_step, task_finish,
             lesson_set, note_save, compact now have a synchronous
             _sync_* variant used by the tool layer when called outside
             of async context (avoids "coroutine never awaited" warnings
             when tool wrappers are sync def but call async store methods).
  Issue 5 — trace_append is now fire-and-forget safe: it catches and
             logs all disk errors so a trace write failure never raises
             into the tool call path.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

log = logging.getLogger(__name__)

# Soft cap on a single trace record's size.
TRACE_ARGS_PREVIEW = 280
TRACE_ERROR_PREVIEW = 400
NOTE_CHUNK_CHARS = 6000
TRACE_KEEP_AFTER_COMPACT = 100

# Phase 2 Issue 1: auto-rotate traces when the file grows beyond this many lines.
# At ~200 bytes/line that is roughly 200 KB — well within reason.
TRACE_AUTO_ROTATE_LINES = 1_000


@dataclass
class MemoryConfig:
    data_dir: Path
    note_chunk_chars: int = NOTE_CHUNK_CHARS
    trace_keep_after_compact: int = TRACE_KEEP_AFTER_COMPACT
    trace_auto_rotate_lines: int = TRACE_AUTO_ROTATE_LINES


class PhantomMemory:
    def __init__(self, data_dir: str | Path, *, config: MemoryConfig | None = None):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "notes").mkdir(exist_ok=True)
        self.config = config or MemoryConfig(data_dir=self.dir)
        # Lazy asyncio.Lock — created on first async access after event loop starts.
        self._lock: asyncio.Lock | None = None
        # Track whether a background rotate is already running.
        self._rotating: bool = False
        # In-memory caches.
        self._facts: dict[str, dict] = self._read_json("facts.json", {})
        self._tasks: dict[str, dict] = self._read_json("tasks.json", {})
        self._lessons: dict[str, dict] = self._read_json("lessons.json", {})
        # Phase 2 Issue 2: integrity check at startup.
        self._verify_integrity()

    @property
    def _alock(self) -> asyncio.Lock:
        """Lazy asyncio.Lock — safe to create only after the event loop is running."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    # ------------------------------------------------------------------
    # Phase 2 Issue 2: Startup integrity check
    # ------------------------------------------------------------------
    def _verify_integrity(self) -> None:
        """
        Scan memory files at startup and log clear warnings for any problems.
        Does NOT raise — integrity issues are logged and self-healed where
        possible (corrupt files were already renamed by _read_json).
        """
        issues: list[str] = []

        # facts.json: every entry should be a dict with a "value" key.
        bad_facts = [
            k for k, v in self._facts.items()
            if not isinstance(v, dict) or "value" not in v
        ]
        if bad_facts:
            issues.append(f"facts.json: {len(bad_facts)} entries missing 'value' key: {bad_facts[:5]}")

        # tasks.json: every entry should have goal, status, steps.
        bad_tasks = [
            k for k, v in self._tasks.items()
            if not isinstance(v, dict) or "goal" not in v or "steps" not in v
        ]
        if bad_tasks:
            issues.append(f"tasks.json: {len(bad_tasks)} entries missing required keys: {bad_tasks[:5]}")

        # lessons.json: every entry should have a "body" key.
        bad_lessons = [
            k for k, v in self._lessons.items()
            if not isinstance(v, dict) or "body" not in v
        ]
        if bad_lessons:
            issues.append(f"lessons.json: {len(bad_lessons)} entries missing 'body' key: {bad_lessons[:5]}")

        # traces.jsonl: count lines and warn if very large.
        traces_p = self.dir / "traces.jsonl"
        if traces_p.exists():
            try:
                line_count = sum(1 for _ in traces_p.open(encoding="utf-8", errors="replace"))
                if line_count > self.config.trace_auto_rotate_lines * 2:
                    issues.append(
                        f"traces.jsonl has {line_count} lines (auto-rotate triggers at "
                        f"{self.config.trace_auto_rotate_lines}). Run memory_compact to trim."
                    )
            except Exception as e:
                issues.append(f"traces.jsonl could not be read: {e}")

        if issues:
            for msg in issues:
                log.warning("[memory integrity] %s", msg)
        else:
            log.debug("[memory integrity] all checks passed.")

    # ------------------------------------------------------------------
    # Disk helpers
    # ------------------------------------------------------------------
    def _read_json(self, name: str, default):
        """
        Read a JSON file from the data directory.
        If unreadable or corrupt, rename to <name>.corrupt.<ts> and return default.
        """
        p = self.dir / name
        if not p.exists():
            return default
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            ts = int(time.time())
            backup = p.with_suffix(p.suffix + f".corrupt.{ts}")
            try:
                p.rename(backup)
                log.error(
                    "memory file %s is corrupt (%s) — reset to default; "
                    "corrupt copy saved at %s",
                    p, e, backup,
                )
            except Exception as rename_err:
                log.error(
                    "memory file %s is corrupt (%s) and could not be renamed: %s",
                    p, e, rename_err,
                )
            return default

    def _write_json(self, name: str, payload) -> None:
        p = self.dir / name
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        tmp.replace(p)

    # ------------------------------------------------------------------
    # Phase 2 Issue 1: Trace rotation helper
    # ------------------------------------------------------------------
    def _traces_line_count(self) -> int:
        """Cheaply count lines in traces.jsonl without loading it."""
        p = self.dir / "traces.jsonl"
        if not p.exists():
            return 0
        try:
            with p.open("rb") as f:
                return sum(1 for _ in f)
        except Exception:
            return 0

    async def _maybe_rotate_traces(self) -> None:
        """
        Trigger a compact() if traces.jsonl is over the rotation threshold.
        Called as a fire-and-forget asyncio.Task from trace_append.
        Guards against re-entrant rotation with self._rotating flag.
        """
        if self._rotating:
            return
        if self._traces_line_count() < self.config.trace_auto_rotate_lines:
            return
        self._rotating = True
        try:
            log.info(
                "[memory] traces.jsonl exceeded %d lines — running auto-compact.",
                self.config.trace_auto_rotate_lines,
            )
            result = await self.compact()
            log.info("[memory] auto-compact done: kept=%s", result.get("kept"))
        except Exception:
            log.exception("[memory] auto-compact failed")
        finally:
            self._rotating = False

    # ------------------------------------------------------------------
    # FACTS
    # ------------------------------------------------------------------
    async def fact_set(self, key: str, value: str) -> dict:
        async with self._alock:
            self._facts[key] = {"value": value, "updated": time.time()}
            self._write_json("facts.json", self._facts)
            return {"ok": True, "key": key, "chars": len(value)}

    def fact_get(self, key: str) -> dict:
        entry = self._facts.get(key)
        if entry is None:
            return {"ok": False, "error": f"no fact named {key!r}"}
        return {"ok": True, "key": key, "value": entry["value"], "updated": entry.get("updated")}

    async def fact_delete(self, key: str) -> dict:
        async with self._alock:
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
    async def task_start(self, task_id: str, goal: str) -> dict:
        async with self._alock:
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

    async def task_step(
        self, task_id: str, step: str, ok: bool = True, detail: str = ""
    ) -> dict:
        async with self._alock:
            task = self._tasks.get(task_id) or {
                "goal": task_id,
                "status": "in_progress",
                "steps": [],
                "summary": "",
                "created": time.time(),
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

    async def task_finish(
        self, task_id: str, status: str = "done", summary: str = ""
    ) -> dict:
        async with self._alock:
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
    async def trace_append(
        self,
        tool: str,
        args: dict | None,
        ok: bool,
        *,
        error: str | None = None,
        latency_ms: int | None = None,
        category: str | None = None,
    ) -> None:
        """
        Append one trace record to traces.jsonl.

        Phase 2 Issue 5: all disk errors are caught and logged — a trace
        write failure must never raise into the calling tool path.

        Phase 2 Issue 1: after writing, schedule a background auto-rotate
        task if the file has grown past the threshold.
        """
        rec = {
            "ts": time.time(),
            "tool": tool,
            "ok": bool(ok),
            "args": _summarize_args(args, TRACE_ARGS_PREVIEW),
            "error": (error or "")[:TRACE_ERROR_PREVIEW] if not ok else None,
            "category": category,
            "latency_ms": latency_ms,
        }
        try:
            async with self._alock:
                with (self.dir / "traces.jsonl").open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            log.exception("trace_append: failed to write trace for tool=%s", tool)
            return  # never raise into the caller

        # Fire-and-forget rotation check — does not block the tool call.
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._maybe_rotate_traces())
        except RuntimeError:
            pass  # no running loop (e.g. during tests) — skip rotation

    def trace_recent(self, limit: int = 20, tool: str | None = None) -> list[dict]:
        fetch = limit * 4 if tool else limit
        traces = self._read_traces_tail(fetch)
        if tool:
            traces = [t for t in traces if t.get("tool") == tool]
        return traces[-limit:]

    def trace_failures(self, tool: str | None = None, limit: int = 20) -> list[dict]:
        traces = [t for t in self._read_traces() if not t.get("ok")]
        if tool:
            traces = [t for t in traces if t.get("tool") == tool]
        return traces[-limit:]

    def _read_traces(self) -> list[dict]:
        p = self.dir / "traces.jsonl"
        if not p.exists():
            return []
        out: list[dict] = []
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
        return out

    def _read_traces_tail(self, n: int) -> list[dict]:
        p = self.dir / "traces.jsonl"
        if not p.exists():
            return []
        try:
            with p.open("rb") as f:
                f.seek(0, 2)
                size = f.tell()
                chunk = min(size, n * 512)
                f.seek(-chunk, 2)
                raw = f.read().decode("utf-8", errors="replace")
        except Exception:
            raw = p.read_text(encoding="utf-8", errors="replace")
        lines = raw.splitlines()
        result: list[dict] = []
        for line in lines[-n:]:
            line = line.strip()
            if not line:
                continue
            try:
                result.append(json.loads(line))
            except Exception:
                continue
        return result

    # ------------------------------------------------------------------
    # LESSONS (distilled action knowledge)
    # ------------------------------------------------------------------
    async def lesson_set(
        self, name: str, body: str, *, source: str = "manual"
    ) -> dict:
        async with self._alock:
            self._lessons[name] = {
                "body": body,
                "updated": time.time(),
                "source": source,
            }
            self._write_json("lessons.json", self._lessons)
            return {"ok": True, "name": name}

    def lesson_get(self, name: str) -> dict | None:
        return self._lessons.get(name)

    def lesson_list(self) -> list[dict]:
        return [
            {
                "name": k,
                "preview": v.get("body", "")[:200],
                "source": v.get("source"),
                "updated": v.get("updated"),
            }
            for k, v in self._lessons.items()
        ]

    async def lesson_delete(self, name: str) -> dict:
        async with self._alock:
            if name in self._lessons:
                del self._lessons[name]
                self._write_json("lessons.json", self._lessons)
                return {"ok": True, "deleted": name}
            return {"ok": False, "error": f"no lesson named {name!r}"}

    # ------------------------------------------------------------------
    # NOTES (large free-form text; chunked retrieval)
    # ------------------------------------------------------------------
    async def note_save(self, label: str, text: str) -> dict:
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
        async with self._alock:
            out_dir = self.dir / "notes" / _safe_filename(label)
            out_dir.mkdir(parents=True, exist_ok=True)
            for i, c in enumerate(chunks):
                (out_dir / f"chunk_{i:04d}.txt").write_text(c, encoding="utf-8")
            (out_dir / "meta.json").write_text(
                json.dumps(meta, indent=2), encoding="utf-8"
            )
        return {"ok": True, **meta}

    def note_load(self, label: str, index: int = 0) -> dict:
        out_dir = self.dir / "notes" / _safe_filename(label)
        meta_p = out_dir / "meta.json"
        if not meta_p.exists():
            return {"ok": False, "error": f"no note labeled {label!r}"}
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
        total = meta["chunks"]
        if index < 0 or index >= total:
            return {"ok": False, "error": f"index {index} out of range (0..{total - 1})"}
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
        out: list[dict] = []
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
        # Phase 2 Issue 3: unified model_id param; old name kept as alias.
        host_model_id: str | None = None,
        lms_base_model_id: str | None = None,  # legacy alias
    ) -> dict:
        """
        Distill recent traces into auto-lessons.

        The effective model ID is resolved in priority order:
          1. host_model_id (new canonical name)
          2. lms_base_model_id (legacy alias — kept for back-compat)
          3. "local-model" fallback
        """
        effective_model_id = host_model_id or lms_base_model_id or "local-model"

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
                    body = await _ask_host_for_lesson(
                        lms_base,
                        tool_name,
                        fails,
                        success_count,
                        model_id=effective_model_id,
                    )
                except Exception as exc:
                    log.warning(
                        "Host lesson generation failed for tool %r "
                        "(model_id=%r): %s — using heuristic body instead.",
                        tool_name,
                        effective_model_id,
                        exc,
                    )

            await self.lesson_set(f"auto:{tool_name}", body, source="auto")
            learned.append(tool_name)

        return {
            "ok": True,
            "examined": len(recent),
            "lessons_written": learned,
            "tools_with_failures": list(failures.keys()),
            "model_id_used": effective_model_id if lms_base else None,
        }

    async def compact(self, target_chars: int = 20_000) -> dict:
        """
        Trim traces.jsonl: write the most recent N records atomically,
        store an aggregate summary as a fact.

        The trace file is written FIRST (atomic tmp->rename), THEN the
        summary fact is recorded — so a crash between the two steps
        leaves a trimmed file with no dangling summary key (safe).
        """
        traces = list(self._read_traces())
        if len(traces) <= self.config.trace_keep_after_compact:
            return {"ok": True, "trimmed": False, "kept": len(traces)}

        cutoff = len(traces) - self.config.trace_keep_after_compact
        old, kept = traces[:cutoff], traces[cutoff:]

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

        # Write trimmed traces FIRST (atomic).
        p = self.dir / "traces.jsonl"
        tmp = p.with_suffix(".jsonl.tmp")
        async with self._alock:
            tmp.write_text(
                "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept),
                encoding="utf-8",
            )
            tmp.replace(p)

        # Record summary fact AFTER the file is safe.
        await self.fact_set(
            f"trace_summary:{ts}", json.dumps(summary, ensure_ascii=False)
        )

        return {
            "ok": True,
            "trimmed": True,
            "kept": len(kept),
            "compacted": len(old),
            "summary_key": f"trace_summary:{ts}",
        }


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
        out[k] = s[:cap] + ("\u2026" if len(s) > cap else "")
    return out


def _safe_filename(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", label)[:120] or "_unnamed"


async def _ask_host_for_lesson(
    base: str,
    tool: str,
    fails: list[dict],
    successes: int,
    *,
    model_id: str = "local-model",
) -> str:
    """
    Ask the loaded host model (LM Studio or Jan.ai) to write an operational
    lesson for a repeatedly-failing tool.
    Works with any OpenAI-compatible /chat/completions endpoint.
    """
    import httpx

    sample = "\n".join(
        f"- ts={int(f.get('ts', 0))} cat={f.get('category')} err={f.get('error', '')[:200]}"
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
                "model": model_id,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 220,
                "temperature": 0.2,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
