"""
safe_call — the one place a tool function is actually invoked.

Responsibilities:
  1. Branch on sync vs async (fixes the original async-in-to_thread bug).
  2. Enforce a per-call timeout.
  3. Retry transient/external failures with exponential backoff + jitter.
  4. Catch everything else and return a ToolResult(ok=False) — never raise.
  5. Stamp meta with timing so observability gets it for free.

This is called by the ToolRegistry (phantom/tools/_base.py). Individual
tools should NOT call safe_call themselves.
"""
from __future__ import annotations

import asyncio
import inspect
import random
import time
from typing import Any, Awaitable, Callable

from phantom.contracts import ToolResult, fail, ok, classify, ErrorCategory

DEFAULT_TIMEOUT_S = 30.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_BASE_S = 0.4
DEFAULT_BACKOFF_CAP_S = 4.0


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
        except asyncio.CancelledError:
            raise  # honor cancellation; do not swallow
        except BaseException as e:  # noqa: BLE001 — we intentionally catch all
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
            # Small jitter so concurrent retries don't stampede.
            await asyncio.sleep(backoff + random.uniform(0, backoff * 0.25))

    # All attempts exhausted.
    elapsed_ms = int((time.monotonic() - started) * 1000)
    msg = _format_error(last_error, tool_name)
    hint = _hint_for(last_category, tool_name)
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
    # Keep it short and model-readable — no traceback.
    summary = str(exc).strip() or exc.__class__.__name__
    if len(summary) > 280:
        summary = summary[:277] + "..."
    return f"{tool_name}: {summary}"


def _hint_for(category: ErrorCategory, tool_name: str) -> str | None:
    if category == ErrorCategory.CLIENT_ERROR:
        return (
            f"Check the arguments you passed to {tool_name}. "
            "The tool's schema shows valid shapes."
        )
    if category == ErrorCategory.EXTERNAL_ERROR:
        return (
            f"{tool_name} depends on an external resource that is unreachable "
            "right now. Try a fallback tool or wait and retry."
        )
    return None
