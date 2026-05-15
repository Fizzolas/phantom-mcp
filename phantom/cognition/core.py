"""
AgentCognition — deterministic reflective scaffolding around PhantomMemory.

This module deliberately avoids any "AGI" claims. It is a small, well-
defined helper that:

  * stores goals and plans inside PhantomMemory.tasks so they survive
    sessions and show up in `memory_task_list`,
  * retrieves the most relevant facts/lessons/recent failures before the
    model picks its next action (so the model is not blindly relying on
    its limited context window),
  * runs a self-check checklist on a draft answer or planned action,
  * classifies the risk of an intended tool call against a small,
    inspectable rule set,
  * records intent → outcome pairs and promotes durable patterns into
    lessons via the existing learning path.

All outputs are bounded by character caps so the runtime token-budget
manager never has to throw away the most useful state.

Concurrency note (Pass 3 Issue 4):
  Methods that perform read-modify-write on per-task plan state
  (plan_advance, plan_set, replan, checkpoint, after_action_review) hold
  a per-task asyncio.Lock while they operate. Read-only methods are
  lock-free. The lock dict itself (_task_locks) is guarded by a small
  meta-lock (_locks_lock) so concurrent first-access for the same
  task_id never races on lock creation.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from typing import Any

from phantom.memory.store import PhantomMemory

# ----------------------------------------------------------------------
# Caps — kept small so the budget manager never has to truncate cognition
# state on tight context models (4k-8k). Increase only if you measure.
# ----------------------------------------------------------------------
PLAN_STEP_MAX = 50
PLAN_STEP_CHARS = 280
GOAL_CHARS = 2000
ACCEPTANCE_CHARS = 2000
STATUS_FACTS_TOP_K = 5
STATUS_LESSONS_TOP_K = 5
STATUS_RECENT_TRACES = 5
STATUS_RECENT_FAILURES = 5

# Confidence bands. Names are deliberately plain so a small LM Studio
# model can reason about them without re-mapping. The numeric score is
# always 0..100.
CONFIDENCE_BANDS = (
    (85, "high"),     # safe to proceed; verify after.
    (60, "moderate"), # proceed with care; surface assumptions.
    (35, "low"),      # gather more info before acting.
    (0,  "very_low"), # do not act; ask user or re-observe.
)

# Stuck-detector heuristics
STUCK_RECENT_WINDOW = 8         # how many trailing traces to inspect
STUCK_FAILURE_THRESHOLD = 3     # >=N failures inside the window = stuck
STUCK_REPEAT_THRESHOLD = 3      # same tool+similar args N times in a row

# Tool-name patterns that suggest an irreversible or high-impact action.
# These are informational only — the cognition layer never blocks a call;
# it just returns a "caution" recommendation the model should heed.
_HIGH_IMPACT_PATTERNS = (
    re.compile(r"^process_kill$"),
    re.compile(r"^shell_(cmd|powershell|python)$"),
    re.compile(r"^file_(write|delete|move|rename)$"),
    re.compile(r"^window_close$"),
    re.compile(r"^desktop_(click|drag|hotkey)$"),
)
_IRREVERSIBLE_TOKENS = (
    "delete", "remove", "rm ", "rm-", "rmdir", "drop ", "format ", "shutdown",
    "reboot", "kill", "uninstall", "git push --force", "git reset --hard",
    "> /dev/", "> nul", "del /f", "rmtree", "truncate",
)


@dataclass(frozen=True)
class RiskAssessment:
    level: str  # "go" | "caution" | "block"
    reasons: list[str]
    irreversible: bool
    touches_user_files: bool


class AgentCognition:
    """Stateless helper bound to a PhantomMemory instance."""

    def __init__(self, memory: PhantomMemory) -> None:
        self.mem = memory
        # Pass 3 Issue 4: per-task locks for read-modify-write methods.
        # _locks_lock guards the dict itself against concurrent first-access
        # for the same task_id.
        self._task_locks: dict[str, asyncio.Lock] = {}
        self._locks_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Internal: acquire the per-task lock (creates it on first use).
    # ------------------------------------------------------------------
    async def _task_lock(self, task_id: str) -> asyncio.Lock:
        async with self._locks_lock:
            if task_id not in self._task_locks:
                self._task_locks[task_id] = asyncio.Lock()
            return self._task_locks[task_id]

    # ------------------------------------------------------------------
    # GOAL + PLAN
    # ------------------------------------------------------------------
    def goal_start(
        self,
        task_id: str,
        goal: str,
        *,
        acceptance: str = "",
        constraints: str = "",
    ) -> dict:
        """
        Start a goal (= a task in PhantomMemory) with optional acceptance
        criteria and constraints. Acceptance + constraints are stored as
        the first plan-step record so the model can recall them later.
        """
        goal = (goal or "").strip()[:GOAL_CHARS]
        if not goal:
            return {"ok": False, "error": "goal must be non-empty"}

        self.mem.task_start(task_id, goal)
        framing_bits = []
        if acceptance:
            framing_bits.append(f"acceptance: {acceptance.strip()[:ACCEPTANCE_CHARS]}")
        if constraints:
            framing_bits.append(f"constraints: {constraints.strip()[:ACCEPTANCE_CHARS]}")
        if framing_bits:
            self.mem.task_step(
                task_id,
                step="goal_framing",
                ok=True,
                detail=" | ".join(framing_bits),
            )
        relevant = self.mem.fact_search(goal, top_k=STATUS_FACTS_TOP_K)
        return {
            "ok": True,
            "task_id": task_id,
            "goal": goal,
            "relevant_facts": relevant,
            "lessons": self.mem.lesson_list()[:STATUS_LESSONS_TOP_K],
            "hint": (
                "Call agent_plan_set next with an ordered list of concrete steps. "
                "Each step should be one tool call or one short observable action."
            ),
        }

    async def plan_set(self, task_id: str, steps: list[str]) -> dict:
        """
        Store an ordered plan. The plan lives as a fact named
        plan:<task_id> so it survives across calls and sessions, and is
        also written as a task step so the task timeline shows it.
        """
        task = self.mem.task_get(task_id)
        if not task.get("ok"):
            return {"ok": False, "error": f"unknown task {task_id!r}"}

        cleaned = [
            (s or "").strip()[:PLAN_STEP_CHARS]
            for s in (steps or [])
            if (s or "").strip()
        ][:PLAN_STEP_MAX]
        if not cleaned:
            return {"ok": False, "error": "plan must have at least one step"}

        lock = await self._task_lock(task_id)
        async with lock:
            plan_doc = {"steps": cleaned, "cursor": 0, "updated": time.time()}
            self.mem.fact_set(f"plan:{task_id}", json.dumps(plan_doc, ensure_ascii=False))
            self.mem.task_step(
                task_id,
                step=f"plan_set ({len(cleaned)} steps)",
                ok=True,
                detail=" | ".join(f"{i+1}. {s}" for i, s in enumerate(cleaned))[:280],
            )
        return {"ok": True, "task_id": task_id, "step_count": len(cleaned)}

    def _load_plan(self, task_id: str) -> dict | None:
        f = self.mem.fact_get(f"plan:{task_id}")
        if not f.get("ok"):
            return None
        try:
            return json.loads(f["value"])
        except Exception:
            return None

    def _save_plan(self, task_id: str, plan: dict) -> None:
        plan["updated"] = time.time()
        self.mem.fact_set(f"plan:{task_id}", json.dumps(plan, ensure_ascii=False))

    async def plan_advance(self, task_id: str, *, step_ok: bool, note: str = "") -> dict:
        """Mark the current step done (or failed) and move the cursor."""
        lock = await self._task_lock(task_id)
        async with lock:
            plan = self._load_plan(task_id)
            if plan is None:
                return {"ok": False, "error": f"no plan stored for {task_id!r}"}
            cursor = plan.get("cursor", 0)
            steps = plan.get("steps", [])
            if cursor >= len(steps):
                return {"ok": True, "task_id": task_id, "done": True, "cursor": cursor}
            current = steps[cursor]
            plan["cursor"] = cursor + 1
            self._save_plan(task_id, plan)
            self.mem.task_step(
                task_id,
                step=f"plan_step[{cursor}]: {current}",
                ok=bool(step_ok),
                detail=note[:PLAN_STEP_CHARS],
            )
        return {
            "ok": True,
            "task_id": task_id,
            "advanced_from": cursor,
            "next_cursor": plan["cursor"],
            "done": plan["cursor"] >= len(steps),
        }

    # ------------------------------------------------------------------
    # CONTEXT PACKETS
    # ------------------------------------------------------------------
    def next_action(self, task_id: str) -> dict:
        """
        Return a compact packet with: current goal, current plan step,
        nearby facts, lessons, and recent failure summaries — everything
        the model needs to decide its next concrete tool call.
        """
        task = self.mem.task_get(task_id)
        if not task.get("ok"):
            return {"ok": False, "error": f"unknown task {task_id!r}"}

        plan = self._load_plan(task_id) or {"steps": [], "cursor": 0}
        steps = plan.get("steps", [])
        cursor = plan.get("cursor", 0)
        current_step = steps[cursor] if 0 <= cursor < len(steps) else None
        upcoming = steps[cursor + 1 : cursor + 4]
        goal_text = task.get("goal", "")
        relevant_facts = self.mem.fact_search(
            f"{goal_text} {current_step or ''}", top_k=STATUS_FACTS_TOP_K
        )
        lessons = self.mem.lesson_list()[:STATUS_LESSONS_TOP_K]
        recent_failures = self.mem.trace_failures(limit=STATUS_RECENT_FAILURES)
        stuck = self.stuck_detect(task_id=task_id)
        return {
            "ok": True,
            "task_id": task_id,
            "goal": goal_text[:GOAL_CHARS],
            "status": task.get("status"),
            "plan_cursor": cursor,
            "plan_total": len(steps),
            "current_step": current_step,
            "upcoming_steps": upcoming,
            "relevant_facts": relevant_facts,
            "lessons": lessons,
            "recent_failures": [
                {
                    "tool": f.get("tool"),
                    "error": (f.get("error") or "")[:200],
                    "category": f.get("category"),
                }
                for f in recent_failures
            ],
            "stuck": stuck,
            "hint": (
                "If stuck.stuck is true, call agent_recover or agent_replan "
                "before issuing the next tool call. Otherwise pick the single "
                "next tool call that advances current_step. If current_step is "
                "null, the plan is exhausted — call agent_after_action_review "
                "and either finish or replan."
            ),
        }

    def status(self, *, task_id: str | None = None) -> dict:
        """A small status packet. Defaults to the most recent active task."""
        if task_id is None:
            tasks = self.mem.task_list(limit=10)
            active = [t for t in tasks if t.get("status") == "in_progress"]
            task_id = active[0]["task_id"] if active else (tasks[0]["task_id"] if tasks else None)
        if task_id is None:
            return {
                "ok": True,
                "task_id": None,
                "message": "no tasks recorded yet — call agent_goal_start to begin.",
                "lessons": self.mem.lesson_list()[:STATUS_LESSONS_TOP_K],
            }
        return self.next_action(task_id)

    # ------------------------------------------------------------------
    # SELF-REFLECTION + RISK CHECK
    # ------------------------------------------------------------------
    def reflect(self, draft: str, *, kind: str = "answer") -> dict:
        """
        Run a deterministic self-check checklist over a draft answer or
        a draft plan/action. Returns concrete questions for the model to
        answer back (the "ouroboros" recursive check). The cognition
        layer never silently rewrites the draft — it surfaces the
        questions and lets the mind decide.
        """
        d = (draft or "").strip()
        if not d:
            return {"ok": False, "error": "draft must be non-empty"}
        checklist = [
            "Does the draft directly address the user's most recent ask?",
            "Are there assumptions in the draft that are not backed by a fact, observation, or tool result?",
            "If the draft proposes an action, is the action reversible? If not, what is the recovery path?",
            "If the draft cites a file path, command, window title, or PID, was it observed via a tool this session, or recalled from memory? If recalled, is it still valid?",
            "Could the draft be wrong because of a stale cache (window titles, file contents, clipboard)? Does it need a fresh observation first?",
            "Is the draft within the user's stated constraints and the active goal's acceptance criteria?",
        ]
        if kind == "action":
            checklist.append(
                "Does the proposed tool call match a step in the current plan, or does it require a plan revision?"
            )
        elif kind == "answer":
            checklist.append(
                "Does the draft tell the user something a follow-up tool call could verify? If so, mention the verification."
            )
        # Cheap heuristic flags so the model can prioritize.
        flags: list[str] = []
        if any(tok in d.lower() for tok in _IRREVERSIBLE_TOKENS):
            flags.append("contains_irreversible_phrasing")
        if "TODO" in d or "FIXME" in d:
            flags.append("contains_unfinished_marker")
        if len(d) > 4000:
            flags.append("draft_is_very_long_consider_chunking")
        # Hedge / uncertainty markers reduce confidence.
        low = d.lower()
        if any(w in low for w in ("i think", "probably", "should be", "i guess", "maybe")):
            flags.append("contains_uncertainty_markers")
        if any(w in low for w in ("safety", "danger", "permanent")):
            flags.append("safety_language_present")

        # Categorize concerns so the caller sees structure, not just a list.
        categories = {
            "correctness": "Are claims backed by observed evidence (facts, recent tool results)?",
            "completeness": "Does the draft cover the user's full ask, including edge cases?",
            "safety": "Could this cause data loss, system damage, or unintended exposure?",
            "reversibility": "If wrong, can the agent undo it without user intervention?",
            "user_intent_alignment": "Does this match the user's stated goal and constraints?",
            "missing_evidence": "What would a follow-up tool call need to verify?",
            "next_verification": "What is the cheapest next observation to confirm or refute the draft?",
        }

        # Confidence band. Heuristic, deterministic.
        confidence = 70  # baseline for a non-empty draft
        if "contains_irreversible_phrasing" in flags:
            confidence -= 25
        if "contains_unfinished_marker" in flags:
            confidence -= 15
        if "contains_uncertainty_markers" in flags:
            confidence -= 10
        if kind == "action":
            confidence -= 5
        confidence = max(0, min(100, confidence))
        band = next(name for thr, name in CONFIDENCE_BANDS if confidence >= thr)
        revise_before_acting = (
            kind == "action" and (band in {"low", "very_low"} or "contains_irreversible_phrasing" in flags)
        )

        return {
            "ok": True,
            "kind": kind,
            "draft_chars": len(d),
            "questions": checklist,
            "categories": categories,
            "flags": flags,
            "confidence_score": confidence,
            "confidence_band": band,
            "revise_before_acting": revise_before_acting,
            "hint": (
                "Answer each question yourself before sending the draft. "
                "If revise_before_acting is true, do NOT act — rewrite or "
                "call agent_understand / agent_action_review first."
            ),
        }

    def risk_check(self, tool_name: str, args: dict[str, Any] | None = None) -> dict:
        """
        Classify the risk of an intended tool call. Returns level=
        'go' | 'caution' | 'block'. The model is expected to honor a
        'block' result by asking the user before proceeding.

        This function never touches the file system or runs the tool.
        """
        args = args or {}
        reasons: list[str] = []
        irreversible = False
        touches_user_files = False
        level = "go"

        is_high = any(p.match(tool_name or "") for p in _HIGH_IMPACT_PATTERNS)
        if is_high:
            reasons.append(f"{tool_name} is in the high-impact tool set")
            level = "caution"

        # Argument heuristics.
        flat = " ".join(str(v) for v in args.values()).lower()
        if any(tok in flat for tok in _IRREVERSIBLE_TOKENS):
            irreversible = True
            reasons.append("arguments contain a phrase typical of irreversible operations")
            level = "caution"

        # File-write / delete on a path that looks user-owned.
        path = args.get("path") or args.get("file") or args.get("dest") or ""
        if isinstance(path, str) and path:
            lower = path.lower()
            if "/users/" in lower or "\\users\\" in lower or lower.startswith("/home/"):
                touches_user_files = True
                reasons.append(f"path '{path}' looks like a user-owned location")
                if tool_name in {"file_delete", "file_write", "file_move", "file_rename"}:
                    level = "caution"
            if any(seg in lower for seg in ("\\windows\\", "/system32", "/etc/", "\\program files")):
                reasons.append(f"path '{path}' targets system directories")
                level = "block"

        # shell commands that look destructive get blocked outright.
        cmd = (args.get("command") or args.get("script") or "")
        if isinstance(cmd, str) and cmd:
            low = cmd.lower()
            if any(p in low for p in ("rm -rf /", "del /f /s /q c:\\", "format c:", "shutdown /r")):
                reasons.append("shell command pattern matches a destructive system operation")
                level = "block"

        if not reasons:
            reasons.append("no risk patterns matched; proceeding is the default")
        return {
            "ok": True,
            "tool": tool_name,
            "level": level,
            "irreversible": irreversible,
            "touches_user_files": touches_user_files,
            "reasons": reasons,
            "hint": {
                "go": "Safe by heuristics. Still verify acceptance after the call.",
                "caution": "Re-read the args; if anything is recalled rather than observed, refresh first.",
                "block": "Ask the user before continuing. Do not call this tool autonomously.",
            }[level],
        }

    # ------------------------------------------------------------------
    # CHECKPOINT + AFTER-ACTION REVIEW
    # ------------------------------------------------------------------
    async def checkpoint(
        self,
        task_id: str,
        *,
        intent: str,
        expected: str,
    ) -> dict:
        """
        Record the model's *intent* before it acts: what it is about to
        do and what it expects to observe. Paired with after_action_review.
        """
        intent = (intent or "").strip()[:PLAN_STEP_CHARS]
        expected = (expected or "").strip()[:PLAN_STEP_CHARS]
        if not intent or not expected:
            return {"ok": False, "error": "intent and expected must be non-empty"}
        cp_id = f"cp:{task_id}:{int(time.time() * 1000)}"
        lock = await self._task_lock(task_id)
        async with lock:
            self.mem.fact_set(
                cp_id,
                json.dumps(
                    {"task_id": task_id, "intent": intent, "expected": expected, "ts": time.time()},
                    ensure_ascii=False,
                ),
            )
            self.mem.task_step(
                task_id,
                step=f"checkpoint: {intent}",
                ok=True,
                detail=f"expected: {expected}",
            )
        return {"ok": True, "checkpoint_id": cp_id}

    async def after_action_review(
        self,
        checkpoint_id: str,
        *,
        observed: str,
        success: bool,
        promote_lesson: bool = True,
    ) -> dict:
        """
        Compare expected vs observed for a previous checkpoint. On
        repeated success of the same intent pattern, optionally promote
        a short lesson the model can read on future calls.
        """
        f = self.mem.fact_get(checkpoint_id)
        if not f.get("ok"):
            return {"ok": False, "error": f"unknown checkpoint {checkpoint_id!r}"}
        try:
            cp = json.loads(f["value"])
        except Exception:
            return {"ok": False, "error": "checkpoint payload was not valid JSON"}

        observed_clipped = (observed or "").strip()[:PLAN_STEP_CHARS]
        task_id = cp.get("task_id")
        intent = cp.get("intent", "")
        expected = cp.get("expected", "")

        lock = await self._task_lock(task_id)
        async with lock:
            diff_summary = _compare(expected, observed_clipped)
            self.mem.task_step(
                task_id,
                step=f"AAR: {intent}",
                ok=bool(success),
                detail=f"expected={expected[:120]} | observed={observed_clipped[:120]} | diff={diff_summary}",
            )

            promoted = None
            if success and promote_lesson:
                recent = [
                    t for t in self.mem.trace_recent(limit=200)
                    if t.get("tool") in {"agent_after_action_review", "agent_checkpoint"}
                ]
                similar = sum(
                    1 for t in recent
                    if (t.get("args") or {}).get("intent", "").startswith(intent[:40])
                )
                if similar >= 2:
                    lesson_name = f"agent:{_slug(intent)[:40]}"
                    lesson_body = (
                        f"When intent is '{intent[:120]}', expecting '{expected[:120]}' "
                        f"reliably worked. Reuse this approach instead of exploring."
                    )
                    self.mem.lesson_set(lesson_name, lesson_body, source="auto-aar")
                    promoted = lesson_name

            # Cleanup: keep facts space tight by deleting checkpoints once reviewed.
            self.mem.fact_delete(checkpoint_id)

        return {
            "ok": True,
            "checkpoint_id": checkpoint_id,
            "task_id": task_id,
            "match": diff_summary,
            "lesson_promoted": promoted,
        }

    # ------------------------------------------------------------------
    # DEEP UNDERSTANDING + CONFIDENCE
    # ------------------------------------------------------------------
    def understand(
        self,
        subject: str,
        *,
        kind: str = "goal",
        task_id: str | None = None,
    ) -> dict:
        """
        Deeply pre-process a goal, action draft, tool-call plan, or answer
        draft *before* it is acted on. Returns a structured packet:
          - intent: one-line restatement of what the model thinks is being asked
          - assumptions: things the draft seems to take for granted
          - missing_info: gaps the model should close before acting
          - likely_consequences: what will probably happen if executed
          - reversibility: 'reversible' | 'partial' | 'irreversible' | 'unknown'
          - evidence: facts/lessons/traces that back the subject
          - questions_to_ask: prompts the model should answer to itself first
          - recommendation: 'proceed' | 'clarify' | 'gather_more_info' | 'do_not_act'

        kind = 'goal' | 'action' | 'answer' | 'plan'.
        """
        s = (subject or "").strip()
        if not s:
            return {"ok": False, "error": "subject must be non-empty"}
        kind = kind if kind in {"goal", "action", "answer", "plan"} else "goal"

        low = s.lower()
        irreversible_hits = [t for t in _IRREVERSIBLE_TOKENS if t in low]
        reversibility = (
            "irreversible" if irreversible_hits
            else "partial" if any(w in low for w in ("overwrite", "replace", "modify"))
            else "reversible" if kind in {"answer", "plan"}
            else "unknown"
        )

        # Evidence retrieval — facts + lessons + recent failures whose tool
        # name or error overlaps with the subject text.
        evidence_facts = self.mem.fact_search(s, top_k=STATUS_FACTS_TOP_K)
        lessons = self.mem.lesson_list()[:STATUS_LESSONS_TOP_K]
        recent_failures = [
            {"tool": f.get("tool"), "error": (f.get("error") or "")[:200]}
            for f in self.mem.trace_failures(limit=STATUS_RECENT_FAILURES)
        ]
        related_failures = [
            f for f in recent_failures
            if f["tool"] and f["tool"].lower() in low
        ]

        # Heuristic assumptions/missing-info detection.
        assumptions: list[str] = []
        missing_info: list[str] = []
        if kind in {"action", "plan"}:
            if any(w in low for w in ("click", "press", "type", "drag")):
                assumptions.append("the correct window/element is currently focused")
                missing_info.append("a fresh screenshot proving the target is on-screen")
            if any(w in low for w in ("file", "read", "write", "delete", "open")):
                assumptions.append("the named file path still exists and is the right one")
                missing_info.append("a recent file_exists / list_dir verification")
            if any(w in low for w in ("kill", "terminate", "shutdown")):
                assumptions.append("killing this process will not lose unsaved user work")
                missing_info.append("user confirmation, or evidence the process is unresponsive")
        if kind == "answer" and not evidence_facts and not related_failures:
            missing_info.append("supporting evidence — answer is not backed by stored facts")

        questions_to_ask = [
            f"Restate the {kind} in one sentence — does the restatement match the user's intent?",
            "Which of the assumptions above are observed (saw it this session) vs recalled (might be stale)?",
            "What single tool call could verify the riskiest assumption cheaply?",
        ]
        if kind == "action":
            questions_to_ask.append(
                "Is this action reversible? If not, has the user explicitly approved it?"
            )

        likely_consequences: list[str] = []
        if irreversible_hits:
            likely_consequences.append(
                "permanent change on disk or to running processes — cannot be undone by the agent"
            )
        if kind == "action" and any(w in low for w in ("click", "type", "press")):
            likely_consequences.append("UI state change in the active window")
        if kind == "answer":
            likely_consequences.append("user trusts the answer; an error here propagates")

        if reversibility == "irreversible" and not evidence_facts:
            recommendation = "do_not_act"
        elif missing_info:
            recommendation = "gather_more_info"
        elif kind == "action" and not evidence_facts and not lessons:
            recommendation = "clarify"
        else:
            recommendation = "proceed"

        plan_alignment = None
        if task_id:
            plan = self._load_plan(task_id) or {"steps": [], "cursor": 0}
            cur = plan["steps"][plan["cursor"]] if plan["steps"] and plan["cursor"] < len(plan["steps"]) else None
            plan_alignment = {
                "current_step": cur,
                "matches_subject": bool(cur and _word_overlap(cur, s) >= 0.3),
            }
            if plan_alignment["current_step"] and not plan_alignment["matches_subject"]:
                missing_info.append(
                    f"subject does not visibly match the active plan step "
                    f"({(cur or '')[:80]!r}) — call agent_replan or agent_plan_advance first"
                )

        return {
            "ok": True,
            "kind": kind,
            "intent": s[:280],
            "assumptions": assumptions,
            "missing_info": missing_info,
            "likely_consequences": likely_consequences,
            "reversibility": reversibility,
            "irreversible_hits": irreversible_hits,
            "evidence": {
                "facts": evidence_facts,
                "lessons": lessons,
                "related_failures": related_failures,
            },
            "questions_to_ask": questions_to_ask,
            "plan_alignment": plan_alignment,
            "recommendation": recommendation,
            "hint": (
                "Answer questions_to_ask before acting. If recommendation is "
                "'do_not_act' or 'clarify', surface a question to the user "
                "instead of calling a tool."
            ),
        }

    def confidence_check(
        self,
        subject: str,
        *,
        kind: str = "action",
        task_id: str | None = None,
        tool_name: str | None = None,
        args: dict[str, Any] | None = None,
    ) -> dict:
        """
        Compute a deterministic 0..100 confidence score for a proposed
        goal/action/answer.
        """
        u = self.understand(subject, kind=kind, task_id=task_id)
        if not u.get("ok"):
            return u

        score = 50

        ev_facts = u["evidence"]["facts"]
        if ev_facts:
            score += min(20, 5 * len(ev_facts))
        if u["evidence"]["lessons"]:
            score += 5

        score -= 10 * len(u["missing_info"])
        if u["reversibility"] == "irreversible":
            score -= 15
        elif u["reversibility"] == "partial":
            score -= 5

        pa = u.get("plan_alignment")
        if pa is not None:
            if pa.get("matches_subject"):
                score += 10
            elif pa.get("current_step"):
                score -= 15

        tool_failure_ratio = None
        if tool_name:
            recent = self.mem.trace_recent(limit=40, tool=tool_name)
            if recent:
                fails = sum(1 for t in recent if not t.get("ok"))
                tool_failure_ratio = fails / max(1, len(recent))
                if tool_failure_ratio >= 0.5:
                    score -= 20
                elif tool_failure_ratio >= 0.25:
                    score -= 10
                elif tool_failure_ratio == 0 and len(recent) >= 5:
                    score += 5

        risk = None
        if kind == "action" and tool_name:
            risk = self.risk_check(tool_name, args or {})
            if risk["level"] == "block":
                score -= 30
            elif risk["level"] == "caution":
                score -= 10

        score = max(0, min(100, score))
        band = next(name for thr, name in CONFIDENCE_BANDS if score >= thr)

        if band == "very_low" or (risk and risk["level"] == "block"):
            decision = "block"
        elif band == "low" or (risk and risk["level"] == "caution"):
            decision = "caution"
        else:
            decision = "go"

        return {
            "ok": True,
            "kind": kind,
            "subject": subject[:280],
            "score": score,
            "band": band,
            "decision": decision,
            "factors": {
                "evidence_facts": len(ev_facts),
                "missing_info": len(u["missing_info"]),
                "reversibility": u["reversibility"],
                "plan_alignment": pa,
                "tool_failure_ratio": tool_failure_ratio,
                "risk_level": (risk or {}).get("level"),
            },
            "recommendation": u["recommendation"],
            "questions_to_ask": u["questions_to_ask"],
            "hint": {
                "go": "Confidence is sufficient. Verify after acting.",
                "caution": "Surface assumptions and re-observe the riskiest one before acting.",
                "block": "Do not act. Ask the user or gather more evidence first.",
            }[decision],
        }

    def action_review(
        self,
        tool_name: str,
        args: dict[str, Any] | None = None,
        *,
        task_id: str | None = None,
        rationale: str = "",
    ) -> dict:
        """
        One-stop pre-action review used right before calling a tool.
        Combines understand(), risk_check(), and confidence_check() into
        a single compact packet so the model only pays one round-trip.
        """
        args = args or {}
        subject = rationale or f"call {tool_name} with {list(args.keys())}"
        u = self.understand(subject, kind="action", task_id=task_id)
        risk = self.risk_check(tool_name, args)
        conf = self.confidence_check(
            subject, kind="action", task_id=task_id, tool_name=tool_name, args=args
        )
        if risk["level"] == "block" or conf["decision"] == "block":
            verdict = "block"
        elif risk["level"] == "caution" or conf["decision"] == "caution":
            verdict = "caution"
        else:
            verdict = "go"
        return {
            "ok": True,
            "tool": tool_name,
            "args_keys": list(args.keys()),
            "task_id": task_id,
            "verdict": verdict,
            "confidence_score": conf["score"],
            "confidence_band": conf["band"],
            "risk_level": risk["level"],
            "reversibility": u["reversibility"],
            "missing_info": u["missing_info"],
            "assumptions": u["assumptions"],
            "questions_to_ask": u["questions_to_ask"],
            "evidence_count": len(u["evidence"]["facts"]),
            "hint": {
                "go": "Cleared by understanding+risk+confidence checks. Proceed and verify the result.",
                "caution": "Re-read args. If anything is recalled rather than observed, refresh before acting.",
                "block": "Do not call this tool. Ask the user or change the plan.",
            }[verdict],
        }

    # ------------------------------------------------------------------
    # STUCK DETECTION + REPLAN/RECOVER
    # ------------------------------------------------------------------
    def stuck_detect(self, task_id: str | None = None) -> dict:
        """
        Detect repeated failures or thrashing and recommend a recovery path.
        """
        traces = self.mem.trace_recent(limit=STUCK_RECENT_WINDOW)
        signals: list[str] = []
        suggestions: list[str] = []

        if not traces:
            return {
                "ok": True,
                "stuck": False,
                "reason": "no recent traces",
                "signals": [],
                "suggestions": ["call a tool — there is nothing to be stuck on yet"],
            }

        fails = [t for t in traces if not t.get("ok")]
        if len(fails) >= STUCK_FAILURE_THRESHOLD:
            signals.append(f"{len(fails)}/{len(traces)} recent calls failed")
            suggestions.append("stop retrying — call agent_replan or ask the user for guidance")

        tool_streak = 1
        for i in range(len(traces) - 1, 0, -1):
            if traces[i].get("tool") == traces[i - 1].get("tool"):
                tool_streak += 1
            else:
                break
        if tool_streak >= STUCK_REPEAT_THRESHOLD:
            signals.append(f"called {traces[-1].get('tool')} {tool_streak} times in a row")
            suggestions.append(
                "vary approach — last N calls used the same tool; try a different one or add a verification step"
            )

        recent_plan_advances = [t for t in traces if t.get("tool") == "agent_plan_advance"]
        if not recent_plan_advances and len(traces) >= 5:
            signals.append("no agent_plan_advance in recent traces")
            suggestions.append(
                "you may be off-plan — call agent_next_action and reconcile, or call agent_plan_advance after each completed step"
            )

        if task_id:
            task = self.mem.task_get(task_id)
            if task.get("ok"):
                recent_steps = (task.get("steps") or [])[-STUCK_RECENT_WINDOW:]
                bad = [s for s in recent_steps if not s.get("ok")]
                if len(bad) >= STUCK_FAILURE_THRESHOLD:
                    signals.append(
                        f"task '{task_id}' has {len(bad)} failed steps in its last "
                        f"{len(recent_steps)} entries"
                    )
                    suggestions.append("call agent_recover to compose a recovery plan")

        return {
            "ok": True,
            "stuck": bool(signals),
            "signals": signals,
            "suggestions": suggestions or ["no stuck-pattern detected"],
        }

    async def replan(
        self,
        task_id: str,
        *,
        reason: str,
        new_steps: list[str] | None = None,
    ) -> dict:
        """
        Replace the current plan with a new ordered list of steps.
        """
        task = self.mem.task_get(task_id)
        if not task.get("ok"):
            return {"ok": False, "error": f"unknown task {task_id!r}"}
        reason_clean = (reason or "").strip()[:PLAN_STEP_CHARS]
        if not reason_clean:
            return {"ok": False, "error": "reason must be non-empty"}
        if new_steps is None:
            old = self._load_plan(task_id) or {"steps": [], "cursor": 0}
            return {
                "ok": True,
                "task_id": task_id,
                "previous_plan": old,
                "stuck_signals": self.stuck_detect(task_id=task_id)["signals"],
                "hint": (
                    "Propose a new ordered list of concrete steps and call "
                    "agent_replan again with new_steps populated."
                ),
            }
        cleaned = [
            (s or "").strip()[:PLAN_STEP_CHARS]
            for s in new_steps
            if (s or "").strip()
        ][:PLAN_STEP_MAX]
        if not cleaned:
            return {"ok": False, "error": "new_steps must contain at least one step"}

        lock = await self._task_lock(task_id)
        async with lock:
            plan_doc = {"steps": cleaned, "cursor": 0, "updated": time.time()}
            self.mem.fact_set(f"plan:{task_id}", json.dumps(plan_doc, ensure_ascii=False))
            self.mem.task_step(
                task_id,
                step=f"replan ({len(cleaned)} steps)",
                ok=True,
                detail=f"reason: {reason_clean}",
            )
        return {
            "ok": True,
            "task_id": task_id,
            "step_count": len(cleaned),
            "reason": reason_clean,
        }

    def recover(self, task_id: str) -> dict:
        """
        Compose a recovery packet for a stalled or failing task. Read-only.
        """
        task = self.mem.task_get(task_id)
        if not task.get("ok"):
            return {"ok": False, "error": f"unknown task {task_id!r}"}
        plan = self._load_plan(task_id) or {"steps": [], "cursor": 0}
        steps = plan.get("steps", [])
        cursor = plan.get("cursor", 0)
        current_step = steps[cursor] if 0 <= cursor < len(steps) else None

        recent_fails = self.mem.trace_failures(limit=STATUS_RECENT_FAILURES)
        failed_tools = {f.get("tool") for f in recent_fails if f.get("tool")}
        relevant_lessons = [
            le for le in self.mem.lesson_list()
            if any(t and t in le["name"] for t in failed_tools)
        ]
        stuck = self.stuck_detect(task_id=task_id)

        if not current_step and steps:
            primary_move = "call agent_after_action_review then agent_replan or finish"
        elif stuck["stuck"]:
            primary_move = "call agent_replan with new_steps that avoid the failing approach"
        else:
            primary_move = "call agent_next_action; the plan may still be valid"

        return {
            "ok": True,
            "task_id": task_id,
            "goal": (task.get("goal") or "")[:GOAL_CHARS],
            "status": task.get("status"),
            "current_step": current_step,
            "recent_failures": [
                {
                    "tool": f.get("tool"),
                    "category": f.get("category"),
                    "error": (f.get("error") or "")[:200],
                }
                for f in recent_fails
            ],
            "relevant_lessons": relevant_lessons[:STATUS_LESSONS_TOP_K],
            "stuck": stuck,
            "primary_move": primary_move,
            "hint": (
                "Choose ONE of: ask the user a focused question, call "
                "agent_replan with new_steps, or pick a different tool. Do "
                "not retry the same call without changing args."
            ),
        }


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def _compare(expected: str, observed: str) -> str:
    """Cheap, deterministic similarity summary for AAR."""
    if not expected and not observed:
        return "no-data"
    e = set(re.findall(r"[A-Za-z0-9_]+", expected.lower()))
    o = set(re.findall(r"[A-Za-z0-9_]+", observed.lower()))
    if not e:
        return "no-expectation"
    overlap = len(e & o)
    score = overlap / max(1, len(e))
    if score >= 0.7:
        return "match"
    if score >= 0.3:
        return "partial"
    return "miss"


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", s.lower()).strip("_") or "unnamed"


_WORD_RE = re.compile(r"[A-Za-z0-9_]+")


def _word_overlap(a: str, b: str) -> float:
    """Jaccard-ish overlap of word sets — used for plan/subject alignment."""
    sa = {w.lower() for w in _WORD_RE.findall(a or "")}
    sb = {w.lower() for w in _WORD_RE.findall(b or "")}
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb) or 1
    return inter / union
