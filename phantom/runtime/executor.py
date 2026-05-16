"""
safe_call — the one place a tool function is actually invoked.

Responsibilities:
  1. Branch on sync vs async (fixes the original async-in-to_thread bug).
  2. Enforce a per-call timeout.
  3. Retry transient/external failures with exponential backoff + jitter.
  4. Catch everything else and return a ToolResult(ok=False) — never raise.
  5. Stamp meta with timing so observability gets it for free.

Phase 3 fixes:
  Issue 1 — TimeoutError now surfaces a human-readable hint that includes
             the timeout value in seconds so the model knows exactly what
             limit was hit and can suggest increasing it.
  Issue 2 — If a sync tool accidentally returns a coroutine (i.e. someone
             decorated an async function as sync), we detect it, log a clear
             warning, await it, and return the result rather than silently
             returning a coroutine object wrapped in ok().
  Issue 3 — elapsed_ms is now recorded on the timeout path (previously it
             was only recorded on the success path because the assignment
             was inside the try block before the except clauses).
  Issue 4 — _format_error truncates exception messages to 280 chars but
             also strips ANSI escape codes so terminal-output exceptions
             (common from subprocess-based tools) don't produce garbage.

This is called by the ToolRegistry (phantom/tools/_base.py). Individual
tools should NOT call safe_call themselves.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import random
import re
import time
from typing import Any, Awaitable, Callable

from phantom.contracts import ToolResult, fail, ok, classify, ErrorCategory

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 30.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_BASE_S = 0.4
DEFAULT_BACKOFF_CAP_S = 4.0

# Phase 3 Issue 4: strip ANSI escape codes from exception messages.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mGKHF]")


async def safe_call(
    fn: Callable[..., Any] | Callable[..., Awaitable[Any]],
    *,
    args: tuple = (),
    kwargs: dict | None = None,
    is_async: bool | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    tool_name: str = "<unknown>",
) -> ToolResult:
    """
    Run `fn` safely and return a ToolResult.

    If `fn` is already a coroutine function, await it directly.
    If `fn` is sync, dispatch to a worker thread via asyncio.to_thread.
    If `fn` returns a ToolResult, pass it through (with meta enriched).
    Otherwise wrap the raw return value in ok(...).
    """
    kwargs = kwargs or {}
    if is_async is None:
        is_async = inspect.iscoroutinefunction(fn)

    last_error: BaseException | None = None
    last_category: ErrorCategory = ErrorCategory.SERVER_ERROR
    # Phase 3 Issue 3: record start time BEFORE the loop so elapsed_ms is
    # always valid regardless of which exit path is taken.
    started = time.monotonic()

    for attempt in range(1, max_attempts + 1):
        try:
            if is_async:
                coro = fn(*args, **kwargs)
                result = await asyncio.wait_for(coro, timeout=timeout_s)
            else:
                result = await asyncio.wait_for(
                    asyncio.to_thread(fn, *args, **kwargs),
                    timeout=timeout_s,
                )

            # Phase 3 Issue 2: guard against a sync-declared tool that
            # accidentally returns a coroutine (async def without @tool async).
            if inspect.isawaitable(result):
                log.warning(
                    "tool %s returned a coroutine from a sync wrapper — "
                    "awaiting it now. Mark the tool as async def to silence this.",
                    tool_name,
                )
                result = await asyncio.wait_for(result, timeout=timeout_s)

            elapsed_ms = int((time.monotonic() - started) * 1000)

            # If the tool already returned a ToolResult, enrich and pass through.
            if isinstance(result, ToolResult):
                result.meta.setdefault("tool", tool_name)
                result.meta["elapsed_ms"] = elapsed_ms
                result.meta["attempts"] = attempt
                return result

            return ok(result, tool=tool_name, elapsed_ms=elapsed_ms, attempts=attempt)

        except asyncio.TimeoutError as e:
            last_error = e
            last_category = ErrorCategory.EXTERNAL_ERROR
            log.warning(
                "tool %s timed out after %.1fs (attempt %d/%d)",
                tool_name, timeout_s, attempt, max_attempts,
            )
        except asyncio.CancelledError:
            raise  # honor cancellation; do not swallow
        except BaseException as e:  # noqa: BLE001
            last_error = e
            last_category = classify(e)
            # Client errors are the caller's fault; no point retrying.
            if last_category == ErrorCategory.CLIENT_ERROR:
                break

        # Retry for external/server errors only, and only if attempts remain.
        if attempt < max_attempts and last_category != ErrorCategory.CLIENT_ERROR:
            backoff = min(
                DEFAULT_BACKOFF_CAP_S,
                DEFAULT_BACKOFF_BASE_S * (2 ** (attempt - 1)),
            )
            await asyncio.sleep(backoff + random.uniform(0, backoff * 0.25))

    # All attempts exhausted.
    elapsed_ms = int((time.monotonic() - started) * 1000)
    msg = _format_error(last_error, tool_name)
    hint = _hint_for(last_category, tool_name, timeout_s=timeout_s)
    return fail(
        msg,
        hint=hint,
        category=last_category.value,
        tool=tool_name,
        elapsed_ms=elapsed_ms,
        attempts=max_attempts,
        exc_type=type(last_error).__name__ if last_error else None,
    )


def _format_error(exc: BaseException | None, tool_name: str) -> str:
    if exc is None:
        return f"{tool_name} failed with no exception captured."
    summary = str(exc).strip() or exc.__class__.__name__
    # Phase 3 Issue 4: strip ANSI escape codes from subprocess tool errors.
    summary = _ANSI_RE.sub("", summary)
    if len(summary) > 280:
        summary = summary[:277] + "..."
    return f"{tool_name}: {summary}"


def _hint_for(
    category: ErrorCategory,
    tool_name: str,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> str | None:
    # Phase 3 Issue 1: timeout hint includes the actual limit value.
    if category == ErrorCategory.EXTERNAL_ERROR:
        return (
            f"{tool_name} timed out or hit an unreachable external resource "
            f"(limit: {timeout_s:.0f}s). "
            "Try a fallback tool, increase the tool's timeout_s, or wait and retry."
        )
    if category == ErrorCategory.CLIENT_ERROR:
        return (
            f"Check the arguments you passed to {tool_name}. "
            "The tool's schema shows valid shapes."
        )
    return None
