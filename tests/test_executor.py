"""Unit tests for safe_call — the fix for the async-in-to_thread bug."""
from __future__ import annotations

import asyncio
import pytest

from phantom.runtime.executor import safe_call
from phantom.contracts import ToolResult, ok


@pytest.mark.asyncio
async def test_sync_tool_returns_ok_envelope():
    def sync_tool(x: int) -> int:
        return x * 2

    r = await safe_call(sync_tool, kwargs={"x": 21}, tool_name="sync_tool")
    assert r.ok is True
    assert r.data == 42
    assert r.meta["tool"] == "sync_tool"
    assert "elapsed_ms" in r.meta


@pytest.mark.asyncio
async def test_async_tool_is_awaited_not_returned_as_coroutine():
    """
    This is the regression test for the original catastrophic bug:
    asyncio.to_thread(async_fn, ...) returned an unawaited coroutine.
    safe_call must branch on is_async and return the resolved value.
    """
    async def async_tool(x: int) -> int:
        await asyncio.sleep(0)  # force at least one suspension
        return x + 100

    r = await safe_call(async_tool, kwargs={"x": 5}, tool_name="async_tool")
    assert r.ok is True
    assert r.data == 105  # NOT a coroutine object


@pytest.mark.asyncio
async def test_client_error_does_not_retry():
    calls = {"n": 0}

    def bad_tool():
        calls["n"] += 1
        raise ValueError("you passed nonsense")

    r = await safe_call(bad_tool, tool_name="bad_tool", max_attempts=3)
    assert r.ok is False
    assert "nonsense" in r.error
    assert r.meta["category"] == "client_error"
    assert calls["n"] == 1  # no retries on client errors


@pytest.mark.asyncio
async def test_external_error_retries():
    calls = {"n": 0}

    def flaky_tool():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("transient")
        return "finally ok"

    r = await safe_call(
        flaky_tool,
        tool_name="flaky",
        max_attempts=4,
    )
    assert r.ok is True
    assert r.data == "finally ok"
    assert r.meta["attempts"] == 3


@pytest.mark.asyncio
async def test_timeout_is_external_error():
    async def slow_tool():
        await asyncio.sleep(5.0)
        return "never"

    r = await safe_call(
        slow_tool,
        tool_name="slow",
        timeout_s=0.05,
        max_attempts=2,
    )
    assert r.ok is False
    assert r.meta["category"] == "external_error"


@pytest.mark.asyncio
async def test_tool_returning_toolresult_is_passed_through():
    def already_envelope():
        return ok("raw", source="already")

    r = await safe_call(already_envelope, tool_name="already")
    assert r.ok is True
    assert r.data == "raw"
    assert r.meta["source"] == "already"
    assert r.meta["tool"] == "already"  # enriched
