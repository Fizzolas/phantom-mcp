"""Unit tests for phantom.cognition.AgentCognition + agent_* tools."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from phantom.memory.store import PhantomMemory
from phantom.cognition.core import AgentCognition


@pytest.fixture
def cog(tmp_path: Path) -> AgentCognition:
    return AgentCognition(PhantomMemory(tmp_path / "phantom_memory"))


# ---- goal + plan ------------------------------------------------------------
def test_goal_start_creates_task_and_returns_relevant_facts(cog):
    cog.mem.fact_set("default_browser", "Firefox 121 on Windows")
    r = cog.goal_start(
        "t1",
        "Open Firefox and visit example.com",
        acceptance="example.com loads in the active window",
    )
    assert r["ok"]
    assert r["task_id"] == "t1"
    keys = {f["key"] for f in r["relevant_facts"]}
    assert "default_browser" in keys
    task = cog.mem.task_get("t1")
    assert task["ok"] and task["status"] == "in_progress"
    assert any("acceptance" in s["detail"] for s in task["steps"])


def test_goal_start_rejects_empty(cog):
    r = cog.goal_start("t1", "   ")
    assert r["ok"] is False


def test_plan_set_and_advance(cog):
    cog.goal_start("t1", "Take a screenshot then describe it")
    plan = cog.plan_set("t1", ["call desktop_screenshot", "call ocr_screen", "summarize"])
    assert plan["ok"] and plan["step_count"] == 3

    a1 = cog.plan_advance("t1", step_ok=True, note="screenshot saved")
    assert a1["ok"] and a1["next_cursor"] == 1 and a1["done"] is False

    a2 = cog.plan_advance("t1", step_ok=True)
    a3 = cog.plan_advance("t1", step_ok=True)
    assert a3["done"] is True


def test_plan_set_unknown_task(cog):
    r = cog.plan_set("nope", ["a"])
    assert r["ok"] is False


def test_plan_set_filters_blank_steps(cog):
    cog.goal_start("t1", "x")
    r = cog.plan_set("t1", ["", "first step", "  ", "second"])
    assert r["ok"] and r["step_count"] == 2


# ---- context packets --------------------------------------------------------
def test_next_action_returns_compact_packet(cog):
    cog.goal_start("t1", "Find the user's resume.docx and summarize it")
    cog.plan_set("t1", ["file_search resume.docx", "read it", "summarize"])
    cog.mem.fact_set("home_dir", "C:/Users/sekri")
    cog.mem.trace_append("file_search", {"q": "x"}, ok=False, error="no match", category="external_error")

    r = cog.next_action("t1")
    assert r["ok"]
    assert r["plan_total"] == 3 and r["plan_cursor"] == 0
    assert r["current_step"].startswith("file_search")
    assert isinstance(r["recent_failures"], list)
    assert r["lessons"] == [] or isinstance(r["lessons"], list)


def test_status_picks_active_task_when_none_specified(cog):
    cog.goal_start("t-old", "old goal")
    cog.mem.task_finish("t-old", status="done")
    cog.goal_start("t-active", "active goal")
    r = cog.status()
    assert r["ok"]
    assert r["task_id"] == "t-active"


def test_status_handles_empty_memory(cog):
    r = cog.status()
    assert r["ok"] and r["task_id"] is None


# ---- reflect ----------------------------------------------------------------
def test_reflect_returns_questions_and_flags(cog):
    r = cog.reflect("I will rm -rf the user's home directory then write a TODO.", kind="action")
    assert r["ok"]
    assert any("reversible" in q.lower() for q in r["questions"])
    assert "contains_irreversible_phrasing" in r["flags"]
    assert "contains_unfinished_marker" in r["flags"]


def test_reflect_answer_kind_adds_verification_question(cog):
    r = cog.reflect("The window is currently focused.", kind="answer")
    assert any("verify" in q.lower() or "verification" in q.lower() for q in r["questions"])


def test_reflect_rejects_empty(cog):
    assert cog.reflect("   ").get("ok") is False


# ---- risk_check -------------------------------------------------------------
def test_risk_check_high_impact_tool_is_caution(cog):
    r = cog.risk_check("process_kill", {"pid": 1234})
    assert r["level"] == "caution"


def test_risk_check_user_path_file_write_is_caution(cog):
    r = cog.risk_check("file_write", {"path": "C:/Users/sekri/notes.txt", "content": "hi"})
    assert r["touches_user_files"] is True
    assert r["level"] == "caution"


def test_risk_check_system_path_blocks(cog):
    r = cog.risk_check("file_write", {"path": "C:/Windows/system32/evil.dll", "content": "x"})
    assert r["level"] == "block"


def test_risk_check_destructive_shell_blocks(cog):
    r = cog.risk_check("shell_cmd", {"command": "rm -rf /"})
    assert r["level"] == "block"


def test_risk_check_safe_default_is_go(cog):
    r = cog.risk_check("clipboard_get", {})
    assert r["level"] == "go"


# ---- checkpoint + AAR -------------------------------------------------------
def test_checkpoint_then_aar_match(cog):
    cog.goal_start("t1", "click the OK button")
    cp = cog.checkpoint("t1", intent="click OK button", expected="dialog closes")
    assert cp["ok"]
    cp_id = cp["checkpoint_id"]
    aar = cog.after_action_review(cp_id, observed="dialog closes successfully", success=True, promote_lesson=False)
    assert aar["ok"] and aar["match"] in {"match", "partial"}
    # checkpoint cleaned up
    assert cog.mem.fact_get(cp_id)["ok"] is False


def test_aar_unknown_checkpoint(cog):
    r = cog.after_action_review("cp:bogus", observed="", success=True)
    assert r["ok"] is False


def test_aar_promote_lesson_after_repeats(cog):
    cog.goal_start("t1", "open notepad")
    # Simulate that the same intent has been seen multiple times.
    for _ in range(3):
        cog.mem.trace_append(
            "agent_checkpoint",
            {"intent": "click OK button on confirmation"},
            ok=True,
        )
    cp = cog.checkpoint("t1", intent="click OK button on confirmation", expected="dialog closes")
    aar = cog.after_action_review(cp["checkpoint_id"], observed="dialog closes", success=True, promote_lesson=True)
    assert aar["lesson_promoted"]
    lesson = cog.mem.lesson_get(aar["lesson_promoted"])
    assert lesson and "click OK button" in lesson["body"]


# ---- registry integration ---------------------------------------------------
@pytest.mark.asyncio
async def test_registry_exposes_agent_tools_and_routes(tmp_path: Path):
    """Smoke test through the real registry — make sure the @tool decorators registered."""
    import phantom.tools  # noqa: F401  forces tool registration
    from phantom.tools._base import registry
    from phantom.tools.memory import set_memory
    from phantom.memory.store import PhantomMemory

    set_memory(PhantomMemory(tmp_path / "phantom_memory"))
    names = {t.name for t in registry.all()}
    for needed in {
        "agent_goal_start",
        "agent_plan_set",
        "agent_plan_advance",
        "agent_next_action",
        "agent_status",
        "agent_reflect",
        "agent_risk_check",
        "agent_checkpoint",
        "agent_after_action_review",
    }:
        assert needed in names, f"missing registered tool: {needed}"

    # End-to-end through the registry.
    r = await registry.call("agent_goal_start", {"task_id": "rt1", "goal": "say hello"})
    assert r.ok and r.data["task_id"] == "rt1"

    r2 = await registry.call("agent_plan_set", {"task_id": "rt1", "steps": ["one", "two"]})
    assert r2.ok and r2.data["step_count"] == 2

    r3 = await registry.call("agent_next_action", {"task_id": "rt1"})
    assert r3.ok and r3.data["plan_total"] == 2

    r4 = await registry.call("agent_risk_check", {"tool": "file_write", "args": {"path": "C:/Windows/system32/x"}})
    assert r4.ok and r4.data["level"] == "block"

    r5 = await registry.call("agent_status", {})
    assert r5.ok


# ---- understand -------------------------------------------------------------
def test_understand_returns_structured_packet(cog):
    cog.mem.fact_set("home_dir", "C:/Users/sekri")
    r = cog.understand("Open the user's resume.docx and summarize", kind="action")
    assert r["ok"]
    assert r["kind"] == "action"
    assert "intent" in r and "assumptions" in r and "missing_info" in r
    assert r["recommendation"] in {"proceed", "clarify", "gather_more_info", "do_not_act"}
    assert r["reversibility"] in {"reversible", "partial", "irreversible", "unknown"}
    assert isinstance(r["questions_to_ask"], list) and r["questions_to_ask"]


def test_understand_irreversible_subject_recommends_caution(cog):
    r = cog.understand("delete C:/Users/sekri/important.docx", kind="action")
    assert r["reversibility"] == "irreversible"
    # No facts back the action -> recommendation should be do_not_act
    assert r["recommendation"] == "do_not_act"


def test_understand_action_with_click_flags_screenshot_need(cog):
    r = cog.understand("click the OK button", kind="action")
    assert any("screenshot" in m.lower() for m in r["missing_info"])


def test_understand_plan_alignment_warns_on_mismatch(cog):
    cog.goal_start("t1", "open notepad and type hello")
    cog.plan_set("t1", ["launch notepad.exe", "type 'hello'", "save"])
    # Subject is unrelated to the current plan step ("launch notepad.exe")
    r = cog.understand("call kill_process for explorer.exe", kind="action", task_id="t1")
    assert r["plan_alignment"] is not None
    assert r["plan_alignment"]["matches_subject"] is False
    assert any("plan" in m.lower() for m in r["missing_info"])


def test_understand_rejects_empty(cog):
    assert cog.understand("   ").get("ok") is False


# ---- confidence_check -------------------------------------------------------
def test_confidence_high_with_evidence_and_aligned_plan(cog):
    cog.mem.fact_set("notepad_window", "Untitled - Notepad")
    cog.goal_start("t1", "type into notepad")
    cog.plan_set("t1", ["type hello into notepad window"])
    r = cog.confidence_check(
        "type hello into notepad window",
        kind="action",
        task_id="t1",
        tool_name="keyboard_type",
        args={"text": "hello"},
    )
    assert r["ok"]
    assert r["band"] in {"moderate", "high"}
    assert r["decision"] in {"go", "caution"}


def test_confidence_low_for_irreversible_without_evidence(cog):
    r = cog.confidence_check(
        "delete C:/Users/sekri/notes.txt",
        kind="action",
        tool_name="file_delete",
        args={"path": "C:/Users/sekri/notes.txt"},
    )
    assert r["score"] < 60
    assert r["decision"] in {"caution", "block"}


def test_confidence_blocks_for_system_path(cog):
    r = cog.confidence_check(
        "write a file under system32",
        kind="action",
        tool_name="file_write",
        args={"path": "C:/Windows/system32/foo.dll", "content": "x"},
    )
    assert r["decision"] == "block"


def test_confidence_drops_when_tool_failure_rate_high(cog):
    # Ten recent failures of the same tool -> failure ratio of 1.0.
    for _ in range(10):
        cog.mem.trace_append("file_search", {"q": "x"}, ok=False, error="no match")
    r = cog.confidence_check(
        "search for resume.docx",
        kind="action",
        tool_name="file_search",
        args={"q": "resume"},
    )
    assert r["factors"]["tool_failure_ratio"] is not None
    assert r["factors"]["tool_failure_ratio"] >= 0.9
    assert r["score"] <= 40


# ---- action_review ----------------------------------------------------------
def test_action_review_blocks_on_destructive_shell(cog):
    r = cog.action_review("shell_cmd", {"command": "rm -rf /"})
    assert r["verdict"] == "block"


def test_action_review_returns_combined_packet(cog):
    r = cog.action_review("clipboard_get", {}, rationale="read clipboard before pasting")
    assert r["ok"]
    assert r["verdict"] in {"go", "caution", "block"}
    assert "confidence_score" in r and "risk_level" in r
    assert isinstance(r["questions_to_ask"], list)


# ---- stuck_detect / replan / recover ---------------------------------------
def test_stuck_detect_flags_repeated_failures(cog):
    for _ in range(4):
        cog.mem.trace_append("desktop_click", {"x": 1, "y": 2}, ok=False, error="window gone")
    r = cog.stuck_detect()
    assert r["stuck"] is True
    assert any("failed" in s for s in r["signals"])


def test_stuck_detect_flags_repeated_tool_streak(cog):
    for _ in range(4):
        cog.mem.trace_append("desktop_click", {"x": 1, "y": 2}, ok=True)
    r = cog.stuck_detect()
    assert r["stuck"] is True
    assert any("desktop_click" in s for s in r["signals"])


def test_stuck_detect_no_traces(cog):
    r = cog.stuck_detect()
    assert r["stuck"] is False


def test_replan_without_new_steps_returns_recommendation(cog):
    cog.goal_start("t1", "open notepad")
    cog.plan_set("t1", ["a", "b"])
    r = cog.replan("t1", reason="b is wrong")
    assert r["ok"]
    assert "previous_plan" in r
    assert r["previous_plan"]["steps"] == ["a", "b"]


def test_replan_with_new_steps_overwrites(cog):
    cog.goal_start("t1", "open notepad")
    cog.plan_set("t1", ["a"])
    r = cog.replan("t1", reason="better approach", new_steps=["x", "y", "z"])
    assert r["ok"] and r["step_count"] == 3
    plan = cog._load_plan("t1")
    assert plan["steps"] == ["x", "y", "z"]
    assert plan["cursor"] == 0


def test_replan_rejects_empty_reason(cog):
    cog.goal_start("t1", "x")
    cog.plan_set("t1", ["a"])
    r = cog.replan("t1", reason="   ", new_steps=["b"])
    assert r["ok"] is False


def test_replan_unknown_task(cog):
    r = cog.replan("nope", reason="x")
    assert r["ok"] is False


def test_recover_returns_packet_with_primary_move(cog):
    cog.goal_start("t1", "click the OK button")
    cog.plan_set("t1", ["focus window", "click OK"])
    for _ in range(4):
        cog.mem.trace_append("desktop_click", {"x": 1}, ok=False, error="off-screen", category="external_error")
    r = cog.recover("t1")
    assert r["ok"]
    assert r["primary_move"]
    assert isinstance(r["recent_failures"], list)
    assert r["stuck"]["stuck"] is True


def test_recover_unknown_task(cog):
    r = cog.recover("nope")
    assert r["ok"] is False


# ---- next_action embeds stuck-detector output ------------------------------
def test_next_action_includes_stuck_signals(cog):
    cog.goal_start("t1", "open notepad")
    cog.plan_set("t1", ["focus", "type"])
    for _ in range(4):
        cog.mem.trace_append("desktop_click", {"x": 1}, ok=False, error="x")
    r = cog.next_action("t1")
    assert "stuck" in r
    assert r["stuck"]["stuck"] is True


# ---- reflect: confidence band + revise signal ------------------------------
def test_reflect_includes_confidence_and_revise(cog):
    r = cog.reflect("I will rm -rf /etc/", kind="action")
    assert r["ok"]
    assert "confidence_score" in r and "confidence_band" in r
    assert r["revise_before_acting"] is True


def test_reflect_safe_answer_has_high_confidence(cog):
    r = cog.reflect("The screenshot shows a dialog with one OK button.", kind="answer")
    assert r["confidence_band"] in {"high", "moderate"}
    assert r["revise_before_acting"] is False


def test_reflect_categories_present(cog):
    r = cog.reflect("draft text", kind="answer")
    assert "categories" in r
    for cat in ("correctness", "completeness", "safety", "reversibility", "user_intent_alignment"):
        assert cat in r["categories"]


# ---- registry integration for new tools ------------------------------------
@pytest.mark.asyncio
async def test_registry_exposes_new_agent_tools(tmp_path: Path):
    import phantom.tools  # noqa: F401
    from phantom.tools._base import registry
    from phantom.tools.memory import set_memory
    from phantom.memory.store import PhantomMemory

    set_memory(PhantomMemory(tmp_path / "phantom_memory"))
    names = {t.name for t in registry.all()}
    for needed in {
        "agent_understand",
        "agent_confidence_check",
        "agent_action_review",
        "agent_stuck_detect",
        "agent_replan",
        "agent_recover",
    }:
        assert needed in names, f"missing tool: {needed}"

    r = await registry.call(
        "agent_understand",
        {"subject": "open notepad", "kind": "action"},
    )
    assert r.ok
    assert r.data["recommendation"] in {"proceed", "clarify", "gather_more_info", "do_not_act"}

    r2 = await registry.call(
        "agent_action_review",
        {"tool": "shell_cmd", "args": {"command": "rm -rf /"}},
    )
    assert r2.ok and r2.data["verdict"] == "block"

    r3 = await registry.call("agent_stuck_detect", {})
    assert r3.ok

    r4 = await registry.call(
        "agent_confidence_check",
        {
            "subject": "click OK in dialog",
            "kind": "action",
            "tool": "desktop_click",
            "args": {"x": 100, "y": 200},
        },
    )
    assert r4.ok
    assert r4.data["band"] in {"high", "moderate", "low", "very_low"}

    # replan flow
    await registry.call("agent_goal_start", {"task_id": "rp", "goal": "x"})
    await registry.call("agent_plan_set", {"task_id": "rp", "steps": ["a"]})
    r5 = await registry.call(
        "agent_replan",
        {"task_id": "rp", "reason": "got better idea", "new_steps": ["b", "c"]},
    )
    assert r5.ok and r5.data["step_count"] == 2

    r6 = await registry.call("agent_recover", {"task_id": "rp"})
    assert r6.ok
