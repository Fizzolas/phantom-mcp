"""Unit tests for the token budget manager."""
from __future__ import annotations

from phantom.runtime.budget import TokenBudget


def test_budget_derives_pools_from_context_length():
    b = TokenBudget(context_length=16384)
    # 40% of 16384 = 6553 tokens for the tool-output pool
    assert 6000 <= b.tool_output_pool <= 7000
    # per-call is 25% of that pool by default
    assert 1500 <= b.per_call_tokens <= 1700


def test_fit_passes_through_short_text():
    b = TokenBudget(context_length=16384)
    text = "hello world"
    out, truncated, original = b.fit(text)
    assert out == text
    assert truncated is False
    assert original == len(text)


def test_fit_truncates_with_sentinel():
    b = TokenBudget(context_length=2048)  # small ctx → small per-call
    text = "x" * 1_000_000  # way larger than any reasonable budget
    out, truncated, original = b.fit(text)
    assert truncated is True
    assert original == 1_000_000
    assert len(out) < len(text)
    assert "truncated" in out  # sentinel present


def test_fit_any_recurses_into_dict():
    b = TokenBudget(context_length=2048)
    payload = {
        "short": "ok",
        "long": "y" * 1_000_000,
        "nested": {"inner": "z" * 1_000_000, "number": 42},
    }
    out, truncated = b.fit_any(payload)
    assert truncated is True
    assert out["short"] == "ok"
    assert out["nested"]["number"] == 42
    assert "truncated" in out["long"]
    assert "truncated" in out["nested"]["inner"]


def test_small_context_still_leaves_minimum_budget():
    b = TokenBudget(context_length=512)  # very small
    # Floors protect us from starving tool output entirely
    assert b.tool_output_pool >= 512
    assert b.per_call_tokens >= 256
