"""
phantom.tools.cognition — agent_* tools that expose AgentCognition to LM Studio.

Naming: every tool is namespaced agent_* so it never collides with the
desktop_*, window_*, file_*, memory_*, etc. tool families. The model
should treat these as the "thinking-about-thinking" surface — small,
cheap calls that help it stay coherent over a long task without
hallucinating.

None of these tools take action on the user's machine. They read and
write phantom memory only.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from phantom.contracts import ok, fail
from phantom.tools._base import tool
from phantom.tools.memory import get_memory
from phantom.cognition.core import AgentCognition


def _cog() -> AgentCognition:
    return AgentCognition(get_memory())


# ---- schemas ----------------------------------------------------------------
class GoalStartIn(BaseModel):
    task_id: str = Field(..., min_length=1, max_length=120)
    goal: str = Field(..., min_length=1, max_length=2000)
    acceptance: str = Field("", max_length=2000)
    constraints: str = Field("", max_length=2000)
    model_config = ConfigDict(extra="forbid")


class PlanSetIn(BaseModel):
    task_id: str = Field(..., min_length=1, max_length=120)
    steps: list[str] = Field(..., min_length=1, max_length=50)
    model_config = ConfigDict(extra="forbid")


class PlanAdvanceIn(BaseModel):
    task_id: str = Field(..., min_length=1, max_length=120)
    step_ok: bool = True
    note: str = Field("", max_length=2000)
    model_config = ConfigDict(extra="forbid")


class TaskIdIn(BaseModel):
    task_id: str = Field(..., min_length=1, max_length=120)
    model_config = ConfigDict(extra="forbid")


class StatusIn(BaseModel):
    task_id: str = Field("", max_length=120)
    model_config = ConfigDict(extra="forbid")


class ReflectIn(BaseModel):
    draft: str = Field(..., min_length=1, max_length=20_000)
    kind: str = Field("answer", pattern="^(answer|action|plan)$")
    model_config = ConfigDict(extra="forbid")


class RiskCheckIn(BaseModel):
    tool: str = Field(..., min_length=1, max_length=120)
    args: dict[str, Any] = Field(default_factory=dict)
    model_config = ConfigDict(extra="forbid")


class CheckpointIn(BaseModel):
    task_id: str = Field(..., min_length=1, max_length=120)
    intent: str = Field(..., min_length=1, max_length=2000)
    expected: str = Field(..., min_length=1, max_length=2000)
    model_config = ConfigDict(extra="forbid")


class AfterActionIn(BaseModel):
    checkpoint_id: str = Field(..., min_length=1, max_length=200)
    observed: str = Field("", max_length=4000)
    success: bool = True
    promote_lesson: bool = True
    model_config = ConfigDict(extra="forbid")


class UnderstandIn(BaseModel):
    subject: str = Field(..., min_length=1, max_length=4000)
    kind: str = Field("goal", pattern="^(goal|action|answer|plan)$")
    task_id: str = Field("", max_length=120)
    model_config = ConfigDict(extra="forbid")


class ConfidenceIn(BaseModel):
    subject: str = Field(..., min_length=1, max_length=4000)
    kind: str = Field("action", pattern="^(goal|action|answer|plan)$")
    task_id: str = Field("", max_length=120)
    tool: str = Field("", max_length=120)
    args: dict[str, Any] = Field(default_factory=dict)
    model_config = ConfigDict(extra="forbid")


class ActionReviewIn(BaseModel):
    tool: str = Field(..., min_length=1, max_length=120)
    args: dict[str, Any] = Field(default_factory=dict)
    task_id: str = Field("", max_length=120)
    rationale: str = Field("", max_length=2000)
    model_config = ConfigDict(extra="forbid")


class StuckDetectIn(BaseModel):
    task_id: str = Field("", max_length=120)
    model_config = ConfigDict(extra="forbid")


class ReplanIn(BaseModel):
    task_id: str = Field(..., min_length=1, max_length=120)
    reason: str = Field(..., min_length=1, max_length=2000)
    new_steps: list[str] | None = Field(default=None, max_length=50)
    model_config = ConfigDict(extra="forbid")


class RecoverIn(BaseModel):
    task_id: str = Field(..., min_length=1, max_length=120)
    model_config = ConfigDict(extra="forbid")


# ---- tools ------------------------------------------------------------------
@tool("agent_goal_start", category="cognition", schema=GoalStartIn, timeout_s=5.0)
def agent_goal_start(
    task_id: str,
    goal: str,
    acceptance: str = "",
    constraints: str = "",
) -> dict:
    """
    Start a goal. Records it as a task in phantom memory and returns the
    most relevant facts and lessons retrieved by goal text. Call
    agent_plan_set next.
    """
    r = _cog().goal_start(
        task_id, goal, acceptance=acceptance, constraints=constraints
    )
    return ok(r) if r.get("ok") else fail(r["error"], category="client_error")


@tool("agent_plan_set", category="cognition", schema=PlanSetIn, timeout_s=5.0)
def agent_plan_set(task_id: str, steps: list[str]) -> dict:
    """
    Store an ordered plan for a task. Each step should be one tool call
    or one short observable action. Plan persists across sessions so
    interruption recovery is possible.
    """
    r = _cog().plan_set(task_id, steps)
    return ok(r) if r.get("ok") else fail(r["error"], category="client_error")


@tool("agent_plan_advance", category="cognition", schema=PlanAdvanceIn, timeout_s=3.0)
def agent_plan_advance(task_id: str, step_ok: bool = True, note: str = "") -> dict:
    """
    Mark the current plan step as completed (or failed) and move the
    cursor to the next step. Call this *after* the actual tool call
    that performed the step.
    """
    r = _cog().plan_advance(task_id, step_ok=step_ok, note=note)
    return ok(r) if r.get("ok") else fail(r["error"], category="client_error")


@tool("agent_next_action", category="cognition", schema=TaskIdIn, timeout_s=5.0)
def agent_next_action(task_id: str) -> dict:
    """
    Return a compact context packet — current plan step, top relevant
    facts, recent failures, lessons — to inform the model's next tool
    call. This is the read side of the proactive/reactive loop.
    """
    r = _cog().next_action(task_id)
    return ok(r) if r.get("ok") else fail(r["error"], category="client_error")


@tool("agent_status", category="cognition", schema=StatusIn, timeout_s=5.0)
def agent_status(task_id: str = "") -> dict:
    """
    Bounded session status packet. If task_id is empty, picks the most
    recent in-progress task. Useful at session start to resume work.
    """
    r = _cog().status(task_id=task_id or None)
    return ok(r) if r.get("ok") else fail(r.get("error", "status failed"), category="client_error")


@tool("agent_reflect", category="cognition", schema=ReflectIn, timeout_s=3.0)
def agent_reflect(draft: str, kind: str = "answer") -> dict:
    """
    Run a self-check checklist on a draft answer or action. Returns a
    list of concrete questions for the model to answer back to itself
    before sending the draft (the recursive / ouroboros check).

    kind = 'answer' | 'action' | 'plan'.
    """
    r = _cog().reflect(draft, kind=kind)
    return ok(r) if r.get("ok") else fail(r["error"], category="client_error")


@tool("agent_risk_check", category="cognition", schema=RiskCheckIn, timeout_s=3.0)
def agent_risk_check(tool: str, args: dict | None = None) -> dict:
    """
    Classify the risk of an intended tool call (level: 'go' | 'caution'
    | 'block') against a small, inspectable rule set. Block means stop
    and ask the user; caution means re-read your args before proceeding.
    """
    r = _cog().risk_check(tool, args or {})
    return ok(r) if r.get("ok") else fail(r.get("error", "risk_check failed"), category="client_error")


@tool("agent_checkpoint", category="cognition", schema=CheckpointIn, timeout_s=3.0)
def agent_checkpoint(task_id: str, intent: str, expected: str) -> dict:
    """
    Record an intent before acting: what you are about to do and what
    you expect to observe. Returns a checkpoint_id to feed into
    agent_after_action_review afterwards.
    """
    r = _cog().checkpoint(task_id, intent=intent, expected=expected)
    return ok(r) if r.get("ok") else fail(r["error"], category="client_error")


@tool(
    "agent_after_action_review",
    category="cognition",
    schema=AfterActionIn,
    timeout_s=5.0,
)
def agent_after_action_review(
    checkpoint_id: str,
    observed: str = "",
    success: bool = True,
    promote_lesson: bool = True,
) -> dict:
    """
    Close out a checkpoint by comparing expected vs observed. On
    repeated success of the same intent pattern, this can promote a
    durable lesson the model will see on future calls.
    """
    r = _cog().after_action_review(
        checkpoint_id,
        observed=observed,
        success=success,
        promote_lesson=promote_lesson,
    )
    return ok(r) if r.get("ok") else fail(r["error"], category="client_error")


@tool("agent_understand", category="cognition", schema=UnderstandIn, timeout_s=5.0)
def agent_understand(subject: str, kind: str = "goal", task_id: str = "") -> dict:
    """
    Deeply pre-process a goal, action draft, plan, or answer before
    acting. Returns intent restatement, assumptions, missing info, likely
    consequences, reversibility, evidence (facts/lessons/related failures),
    questions to ask, and a recommendation
    ('proceed' | 'clarify' | 'gather_more_info' | 'do_not_act').

    Use this BEFORE calling a tool when you are not confident, or before
    sending a non-trivial answer.
    """
    r = _cog().understand(subject, kind=kind, task_id=task_id or None)
    return ok(r) if r.get("ok") else fail(r["error"], category="client_error")


@tool("agent_confidence_check", category="cognition", schema=ConfidenceIn, timeout_s=5.0)
def agent_confidence_check(
    subject: str,
    kind: str = "action",
    task_id: str = "",
    tool: str = "",
    args: dict | None = None,
) -> dict:
    """
    Compute a 0..100 confidence score and band ('high'|'moderate'|'low'|
    'very_low') for a proposed goal/action/answer, plus a 'go'|'caution'|
    'block' decision. Combines evidence strength, plan alignment, recent
    tool failure rate, and risk signals.
    """
    r = _cog().confidence_check(
        subject,
        kind=kind,
        task_id=task_id or None,
        tool_name=tool or None,
        args=args or {},
    )
    return ok(r) if r.get("ok") else fail(r["error"], category="client_error")


@tool("agent_action_review", category="cognition", schema=ActionReviewIn, timeout_s=5.0)
def agent_action_review(
    tool: str,
    args: dict | None = None,
    task_id: str = "",
    rationale: str = "",
) -> dict:
    """
    One-stop pre-action review combining understand + risk_check +
    confidence_check. Use this immediately before calling any
    consequential tool. Returns a 'go'|'caution'|'block' verdict and the
    questions you should answer to yourself first.
    """
    r = _cog().action_review(
        tool,
        args or {},
        task_id=task_id or None,
        rationale=rationale,
    )
    return ok(r) if r.get("ok") else fail(r.get("error", "action_review failed"), category="client_error")


@tool("agent_stuck_detect", category="cognition", schema=StuckDetectIn, timeout_s=3.0)
def agent_stuck_detect(task_id: str = "") -> dict:
    """
    Heuristic detector for 'agent is thrashing': repeated failures,
    same tool called many times in a row, no plan advancement. Returns
    signals and concrete suggestions. Read-only.
    """
    r = _cog().stuck_detect(task_id=task_id or None)
    return ok(r) if r.get("ok") else fail(r.get("error", "stuck_detect failed"), category="client_error")


@tool("agent_replan", category="cognition", schema=ReplanIn, timeout_s=5.0)
def agent_replan(task_id: str, reason: str, new_steps: list[str] | None = None) -> dict:
    """
    Replace the current plan for a task. Pass new_steps=None first to get
    a recommendation packet (previous plan + stuck signals), then call
    again with new_steps populated to commit the new plan. The reason is
    written to the task timeline.
    """
    r = _cog().replan(task_id, reason=reason, new_steps=new_steps)
    return ok(r) if r.get("ok") else fail(r["error"], category="client_error")


@tool("agent_recover", category="cognition", schema=RecoverIn, timeout_s=5.0)
def agent_recover(task_id: str) -> dict:
    """
    Compose a recovery packet for a stalled or failing task. Read-only.
    Surfaces the goal, current step, recent failures, matching lessons,
    stuck signals, and a primary suggested move. Use when next_action
    reports stuck=true, or when a tool has failed multiple times.
    """
    r = _cog().recover(task_id)
    return ok(r) if r.get("ok") else fail(r["error"], category="client_error")
