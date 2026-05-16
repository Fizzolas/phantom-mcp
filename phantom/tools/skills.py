"""
phantom.tools.skills — skill_* tools for the Ouro self-improving layer.

A "skill" is a named, versioned piece of behaviour the model has learned
through repetition and self-review: a reusable prompt fragment, a preferred
sequence of tool calls, or a domain-specific procedure. Skills are stored
persistently in phantom memory (facts namespace, key prefix 'skill:') so
they survive across sessions.

How skills differ from lessons:
  - Lessons: "what went wrong; here's what to check next time" — reactive.
  - Skills:  "I can do X reliably using this procedure" — proactive.

The Ouro (ouroboros) loop works as follows:
  1. model uses agent_action_review / agent_understand before acting.
  2. agent_after_action_review records what actually happened.
  3. After enough successes, the model calls skill_promote to crystallise
     the procedure into a skill.
  4. At session start, skill_load_relevant returns applicable skills for
     the current goal so the model starts informed.
  5. When a skill consistently underperforms, skill_deprecate retires it
     and optionally triggers memory_learn_from_traces to rewrite the lesson.

All skill_* tools are pure memory I/O — they never touch the desktop,
run shell commands, or modify any file outside data/phantom_memory/.
"""
from __future__ import annotations

import json
import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from phantom.contracts import ok as _ok, fail
from phantom.tools._base import tool
from phantom.tools.memory import get_memory


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class SkillPromoteIn(BaseModel):
    """Promote a procedure to a named skill."""
    name: str = Field(
        ..., min_length=1, max_length=120,
        description=(
            "Short slug for the skill, e.g. 'open_browser_to_url', "
            "'type_in_search_box', 'read_pdf_via_ocr'. Use lowercase with underscores."
        ),
    )
    description: str = Field(
        ..., min_length=1, max_length=400,
        description="One sentence: what does this skill do and when should it be used?",
    )
    steps: list[str] = Field(
        ..., min_length=1, max_length=30,
        description=(
            "Ordered list of tool calls or actions that make up this skill. "
            "Each step should be a short sentence, e.g. "
            "'Call window_list, find the browser window title, call window_focus.'"
        ),
    )
    tags: list[str] = Field(
        default_factory=list, max_length=10,
        description="Optional tags for retrieval: e.g. ['browser', 'navigation', 'ocr'].",
    )
    source_task_id: str = Field(
        "", max_length=120,
        description="Optional: the task_id this skill was derived from.",
    )
    model_config = ConfigDict(extra="forbid")


class SkillGetIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    model_config = ConfigDict(extra="forbid")


class SkillSearchIn(BaseModel):
    query: str = Field(..., min_length=1, max_length=400)
    top_k: int = Field(5, ge=1, le=20)
    model_config = ConfigDict(extra="forbid")


class SkillLoadRelevantIn(BaseModel):
    goal: str = Field(
        ..., min_length=1, max_length=2000,
        description="The current goal or task description. Used to retrieve relevant skills.",
    )
    top_k: int = Field(5, ge=1, le=20)
    model_config = ConfigDict(extra="forbid")


class SkillRecordOutcomeIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    success: bool = Field(...)
    note: str = Field("", max_length=1000, description="Optional: what happened.")
    model_config = ConfigDict(extra="forbid")


class SkillDeprecateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    reason: str = Field(..., min_length=1, max_length=400)
    model_config = ConfigDict(extra="forbid")


class SkillImproveIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    updated_steps: list[str] = Field(..., min_length=1, max_length=30)
    improvement_note: str = Field("", max_length=400)
    model_config = ConfigDict(extra="forbid")


class NoneIn(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _skill_key(name: str) -> str:
    return f"skill:{name}"


def _get_skill(name: str) -> dict | None:
    mem = get_memory()
    r = mem.fact_get(_skill_key(name))
    if not r.get("ok"):
        return None
    try:
        return json.loads(r["value"])
    except Exception:
        return None


def _skill_list_all() -> list[dict]:
    mem = get_memory()
    all_keys = mem.fact_list()
    skills = []
    for key in all_keys:
        if not key.startswith("skill:"):
            continue
        r = mem.fact_get(key)
        if not r.get("ok"):
            continue
        try:
            s = json.loads(r["value"])
            if s.get("status") != "deprecated":
                skills.append(s)
        except Exception:
            continue
    return skills


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool("skill_promote", category="skill", schema=SkillPromoteIn, timeout_s=5.0)
async def skill_promote(
    name: str,
    description: str,
    steps: list[str],
    tags: list[str] | None = None,
    source_task_id: str = "",
) -> dict:
    """
    Crystallise a procedure you have successfully repeated into a named skill.

    After completing a multi-step task reliably (2+ successes with the same
    approach), call this tool to record the procedure so you can recall and
    reuse it in future sessions without rethinking from scratch.

    The skill is stored permanently in phantom memory. Use skill_load_relevant
    at session start to pull back skills matching your current goal.

    Returns the saved skill record including its version and use_count=0.
    """
    mem = get_memory()
    existing = _get_skill(name)
    version = (existing.get("version", 0) + 1) if existing else 1

    record = {
        "name": name,
        "description": description,
        "steps": steps,
        "tags": tags or [],
        "source_task_id": source_task_id,
        "version": version,
        "use_count": 0,
        "success_count": 0,
        "fail_count": 0,
        "status": "active",
        "created": existing.get("created", time.time()) if existing else time.time(),
        "updated": time.time(),
    }
    await mem.fact_set(_skill_key(name), json.dumps(record, ensure_ascii=False))
    return _ok({"saved": record})


@tool("skill_get", category="skill", schema=SkillGetIn, timeout_s=3.0)
def skill_get(name: str) -> dict:
    """
    Load the full record for a named skill including its steps, stats, and version.

    Use this to review a skill before deciding to improve or deprecate it,
    or to copy its steps into your current plan.
    """
    record = _get_skill(name)
    if record is None:
        return fail(f"No skill named {name!r}.", category="client_error")
    return _ok(record)


@tool("skill_list", category="skill", schema=NoneIn, timeout_s=3.0)
def skill_list() -> dict:
    """
    List all active skills with their name, description, version, use_count,
    success_count, fail_count, and tags.

    Use at session start together with skill_load_relevant to orient yourself
    to what you already know how to do.
    """
    skills = _skill_list_all()
    summary = [
        {
            "name": s["name"],
            "description": s.get("description", ""),
            "version": s.get("version", 1),
            "use_count": s.get("use_count", 0),
            "success_count": s.get("success_count", 0),
            "fail_count": s.get("fail_count", 0),
            "tags": s.get("tags", []),
            "status": s.get("status", "active"),
        }
        for s in skills
    ]
    return _ok({"skills": summary, "total": len(summary)})


@tool("skill_search", category="skill", schema=SkillSearchIn, timeout_s=5.0)
def skill_search(query: str, top_k: int = 5) -> dict:
    """
    Search skills by word overlap against their name, description, and tags.

    Returns matching skills ranked by relevance with name, description,
    version, and tags. Use skill_get to load the full steps for a match.
    """
    from phantom.memory.store import _tokenize  # reuse the same BM25-style scorer
    q_terms = _tokenize(query)
    if not q_terms:
        return _ok({"matches": []})

    skills = _skill_list_all()
    scored = []
    for s in skills:
        text = f"{s.get('name','')} {s.get('description','')} {' '.join(s.get('tags',[]))}"
        terms = _tokenize(text)
        overlap = len(q_terms & terms)
        if overlap == 0:
            continue
        score = overlap / (len(q_terms) + len(terms) - overlap)
        scored.append((score, s))
    scored.sort(key=lambda x: x[0], reverse=True)

    return _ok({
        "matches": [
            {
                "name": s["name"],
                "score": round(sc, 3),
                "description": s.get("description", ""),
                "version": s.get("version", 1),
                "tags": s.get("tags", []),
            }
            for sc, s in scored[:top_k]
        ]
    })


@tool("skill_load_relevant", category="skill", schema=SkillLoadRelevantIn, timeout_s=5.0)
def skill_load_relevant(goal: str, top_k: int = 5) -> dict:
    """
    Retrieve skills most relevant to the current goal and return their full
    steps ready to use in your plan.

    Call this at the start of any non-trivial task (after agent_goal_start)
    to see if Phantom already knows a proven procedure for this goal.
    If a returned skill's steps match your plan, you can adopt them directly
    instead of planning from scratch — saving context and improving reliability.

    Returns a list of skills with full steps, sorted by relevance score.
    """
    from phantom.memory.store import _tokenize
    q_terms = _tokenize(goal)
    skills = _skill_list_all()
    scored = []
    for s in skills:
        text = f"{s.get('name','')} {s.get('description','')} {' '.join(s.get('tags',[]))}"
        terms = _tokenize(text)
        overlap = len(q_terms & terms)
        if overlap == 0:
            continue
        score = overlap / (len(q_terms) + len(terms) - overlap)
        scored.append((score, s))
    scored.sort(key=lambda x: x[0], reverse=True)

    return _ok({
        "relevant_skills": [
            {
                "name": s["name"],
                "score": round(sc, 3),
                "description": s.get("description", ""),
                "steps": s.get("steps", []),
                "tags": s.get("tags", []),
                "version": s.get("version", 1),
                "success_count": s.get("success_count", 0),
                "fail_count": s.get("fail_count", 0),
            }
            for sc, s in scored[:top_k]
        ],
        "hint": (
            "If a skill's steps match your plan, adopt them directly. "
            "Call skill_record_outcome after completing the task to keep stats up to date."
        ),
    })


@tool("skill_record_outcome", category="skill", schema=SkillRecordOutcomeIn, timeout_s=3.0)
async def skill_record_outcome(name: str, success: bool, note: str = "") -> dict:
    """
    Record whether a skill execution succeeded or failed.

    Call this after completing (or abandoning) a task where you used a skill.
    Stats accumulate over time: the model and any user reviewing the skills
    can see which procedures are reliable and which need improvement.

    High fail_count relative to use_count is a signal to call skill_improve
    or skill_deprecate.
    """
    record = _get_skill(name)
    if record is None:
        return fail(f"No skill named {name!r}.", category="client_error")

    record["use_count"] = record.get("use_count", 0) + 1
    if success:
        record["success_count"] = record.get("success_count", 0) + 1
    else:
        record["fail_count"] = record.get("fail_count", 0) + 1
    if note:
        record["last_outcome_note"] = note[:400]
    record["last_used"] = time.time()
    record["updated"] = time.time()

    mem = get_memory()
    await mem.fact_set(_skill_key(name), json.dumps(record, ensure_ascii=False))
    return _ok({
        "name": name,
        "success": success,
        "use_count": record["use_count"],
        "success_count": record["success_count"],
        "fail_count": record["fail_count"],
    })


@tool("skill_improve", category="skill", schema=SkillImproveIn, timeout_s=5.0)
async def skill_improve(
    name: str,
    updated_steps: list[str],
    improvement_note: str = "",
) -> dict:
    """
    Replace the steps in an existing skill with an improved version.

    Use this when a skill that worked before has started failing due to UI
    changes, new app versions, or when you discover a better procedure.
    The version number increments on each improvement so you can track how
    the skill has evolved. Previous step history is NOT kept — only the
    latest version is active.

    After improving, call skill_record_outcome after your next use to
    confirm the improvement actually helped.
    """
    record = _get_skill(name)
    if record is None:
        return fail(f"No skill named {name!r}.", category="client_error")

    record["steps"] = updated_steps
    record["version"] = record.get("version", 1) + 1
    record["status"] = "active"
    if improvement_note:
        record["improvement_note"] = improvement_note[:400]
    record["updated"] = time.time()

    mem = get_memory()
    await mem.fact_set(_skill_key(name), json.dumps(record, ensure_ascii=False))
    return _ok({"name": name, "new_version": record["version"], "steps": updated_steps})


@tool("skill_deprecate", category="skill", schema=SkillDeprecateIn, timeout_s=3.0)
async def skill_deprecate(name: str, reason: str) -> dict:
    """
    Mark a skill as deprecated so it no longer appears in skill_list or
    skill_load_relevant results.

    Use this when:
      - A skill's fail_count has grown high and skill_improve hasn't helped.
      - The procedure it describes is no longer needed or no longer possible
        (e.g. an app was uninstalled; a button moved in a UI update).
      - You are replacing it with a newer, better-named skill.

    The skill record is kept in memory (not deleted) for audit purposes.
    The reason is stored for future reference.
    """
    record = _get_skill(name)
    if record is None:
        return fail(f"No skill named {name!r}.", category="client_error")

    record["status"] = "deprecated"
    record["deprecation_reason"] = reason[:400]
    record["deprecated_at"] = time.time()
    record["updated"] = time.time()

    mem = get_memory()
    await mem.fact_set(_skill_key(name), json.dumps(record, ensure_ascii=False))
    return _ok({"deprecated": name, "reason": reason})
