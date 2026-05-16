"""
phantom-mcp server v2 — slim entry point built on the phantom registry.

What changed vs server.py:
  * No 1500-line dispatch table. Tools are imported once; the
    ToolRegistry routes calls and validates arguments via pydantic.
  * Tool surface is shaped at boot by the capability probe — tools
    whose deps are missing don't appear in list_tools, so the host
    model never tries to call them.
  * Every tool result is shaped as ToolResult (ok/data/error/hint/meta).
    Outputs that exceed the per-call token budget are truncated with a
    sentinel rather than silently dropping data.
  * Every tool call is recorded in PhantomMemory traces (tool name, args
    summary, ok, error, latency). memory_learn_from_traces distills
    repeated failures into lessons the model can read at boot.
  * Host detection: at boot we probe for LM Studio, Jan.ai, and any
    generic OpenAI-compatible server. The first reachable one wins and
    sizes the token budget. If none are found we fall back to 8k context
    and keep running — all tools that don't need the host stay available.

STDIO SAFETY NOTE
-----------------
MCP uses stdin/stdout as a transport. ANY byte written to stdout before
the MCP framing loop starts will corrupt the handshake. Jan.ai (and
other strict hosts) immediately close the connection when they see
unexpected bytes — this is what causes the crash on Jan.

To prevent this we redirect Python's sys.stdout to sys.stderr as the
very first action, before any import that might print(). All logging
goes to a file + stderr only. The MCP library writes to the raw
stdin/stdout file descriptors directly, bypassing sys.stdout, so it
is unaffected by this redirect.

Host port map (auto-detected, override with env vars):
  LM Studio   : http://localhost:1234/v1   (PHANTOM_HOST_URL to override)
  Jan.ai      : http://localhost:1337/v1   (PHANTOM_HOST_URL to override)
  Generic     : PHANTOM_HOST_URL if set, otherwise skipped

Run:
    python server_v2.py

Force a specific host (useful if auto-detect picks the wrong one):
    PHANTOM_HOST_URL=http://localhost:1337/v1 python server_v2.py
"""
from __future__ import annotations

import sys

# --- STDIO SAFETY: redirect stdout to stderr BEFORE any other import --------
# MCP transport owns stdout. Any stray print() or import-time output will
# corrupt the JSON-RPC framing and cause Jan.ai / other strict hosts to
# immediately kill the connection. Redirecting sys.stdout to sys.stderr here
# means accidental prints go to the log stream, not the MCP pipe.
# This must happen before every other import.
_original_stdout = sys.stdout
sys.stdout = sys.stderr
# ---------------------------------------------------------------------------

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

# ---- logging ---------------------------------------------------------------
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

log = logging.getLogger("phantom")
log.setLevel(logging.DEBUG)
log.handlers.clear()

_file = logging.FileHandler(LOG_DIR / "server_v2.log", encoding="utf-8")
_file.setLevel(logging.DEBUG)
_file.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

_stderr = logging.StreamHandler(sys.stderr)
_stderr.setLevel(logging.WARNING)
_stderr.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

log.addHandler(_file)
log.addHandler(_stderr)
log.propagate = False

log.info("phantom-mcp v2 starting...")

# ---- phantom internals -----------------------------------------------------
from phantom.contracts import ToolResult, fail
from phantom.runtime.budget import TokenBudget
from phantom.runtime.capabilities import probe_capabilities
from phantom.runtime.lmstudio import probe_lmstudio, LMStudioProbe, FALLBACK_CONTEXT_LENGTH
import phantom.tools  # registers everything via @tool decorators
from phantom.tools._base import registry
from phantom.tools.memory import get_memory, set_memory
from phantom.memory.store import PhantomMemory


# ---- boot config ------------------------------------------------------------
DATA_DIR = ROOT / "data" / "phantom_memory"
set_memory(PhantomMemory(DATA_DIR))

_BUDGET: TokenBudget | None = None
_LMS_INFO: dict | None = None

# Known host candidates: (label, base_url)
# Jan.ai uses port 1337; LM Studio uses 1234.
# Both speak the same OpenAI-compatible REST schema.
_HOST_CANDIDATES: list[tuple[str, str]] = [
    ("LM Studio",  "http://localhost:1234/v1"),
    ("Jan.ai",     "http://localhost:1337/v1"),
]


async def _detect_host() -> LMStudioProbe:
    """
    Try hosts in order and return the first reachable one.

    Priority:
      1. PHANTOM_HOST_URL env var (explicit override — always tried first).
      2. LM Studio  (port 1234)
      3. Jan.ai     (port 1337)
      4. Safe fallback probe (reachable=False, 8k context).

    The probe uses probe_lmstudio() which already handles SDK vs REST
    fallback and caching — we just feed it different base_url values.
    """
    # 1. Explicit override from environment.
    override_url = os.environ.get("PHANTOM_HOST_URL", "").strip()
    if override_url:
        log.info("host detection: PHANTOM_HOST_URL override → %s", override_url)
        result = await probe_lmstudio(base_url=override_url, force=True)
        if result.reachable:
            log.info("host detected via override: %s (model=%s ctx=%s)",
                     override_url, result.model_id, result.context_length)
            return result
        log.warning("PHANTOM_HOST_URL=%s is not reachable: %s", override_url, result.error)

    # 2. Auto-detect from candidate list.
    candidates = list(_HOST_CANDIDATES)
    for label, url in candidates:
        result = await probe_lmstudio(base_url=url, force=True)
        if result.reachable:
            log.info("host detected: %s at %s (model=%s ctx=%s tools=%s)",
                     label, url, result.model_id, result.context_length, result.supports_tools)
            return result
        log.debug("host probe miss: %s (%s) → %s", label, url, result.error)

    # 3. Nothing reachable — safe degraded fallback.
    log.warning(
        "No host reachable (tried LM Studio:1234, Jan.ai:1337%s). "
        "Running in offline mode — tools that don't need a host stay available. "
        "Set PHANTOM_HOST_URL env var if your host is on a non-standard port.",
        f", override={override_url}" if override_url else "",
    )
    return LMStudioProbe(
        reachable=False,
        context_length=FALLBACK_CONTEXT_LENGTH,
        error="no reachable host found",
    )


async def _refresh_runtime_state() -> None:
    """Probe capabilities + host; size the budget for the loaded model."""
    global _BUDGET, _LMS_INFO

    caps = probe_capabilities()
    registry.set_capabilities(caps)

    host_probe = await _detect_host()
    _LMS_INFO = host_probe.as_dict()
    _BUDGET = TokenBudget(context_length=host_probe.context_length)
    log.info(
        "boot: capabilities=%s host_reachable=%s model=%s ctx=%s",
        sorted(caps), host_probe.reachable, host_probe.model_id, host_probe.context_length,
    )


# ---- MCP server ------------------------------------------------------------
app = Server("phantom-mcp")


class _OnceFilter(logging.Filter):
    def __init__(self) -> None:
        super().__init__()
        self._fired = False

    def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        if self._fired:
            return False
        self._fired = True
        return True


_list_tools_once = _OnceFilter()


def _spec_to_mcp_tool(spec) -> types.Tool:
    """Translate a registry ToolSpec into the MCP types.Tool the client sees."""
    schema = spec.json_schema()
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    if spec.schema is not None:
        schema.setdefault("additionalProperties", False)
    return types.Tool(
        name=spec.name,
        description=spec.description or "(no description provided)",
        inputSchema=schema,
    )


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    available = registry.available()
    _once_log = logging.getLogger("phantom.list_tools.once")
    if not _once_log.filters:
        _once_log.addFilter(_list_tools_once)
    _once_log.info(
        "list_tools: %d tools advertised (of %d total)", len(available), len(registry.all())
    )
    return [_spec_to_mcp_tool(s) for s in available]


@app.call_tool()
async def call_tool(name: str, arguments: dict | None = None) -> list[types.TextContent]:
    args = arguments or {}
    started = time.monotonic()
    log.debug("call: %s args=%s", name, list(args.keys()))

    result: ToolResult = await registry.call(name, args)

    try:
        get_memory().trace_append(
            tool=name,
            args=args,
            ok=result.ok,
            error=result.error,
            latency_ms=int((time.monotonic() - started) * 1000),
            category=result.meta.get("category") if isinstance(result.meta, dict) else None,
        )
    except Exception:
        log.exception("trace_append failed for %s", name)

    payload = result.to_dict()

    if _BUDGET is not None:
        try:
            data, was_truncated = _BUDGET.fit_any(payload.get("data"))
            payload["data"] = data
            if was_truncated:
                payload.setdefault("meta", {})["truncated"] = True
        except Exception:
            log.exception("budget.fit_any failed for %s", name)

    if (
        payload.get("ok") is True
        and isinstance(payload.get("data"), str)
        and not payload.get("meta")
        and not payload.get("error")
        and not payload.get("hint")
    ):
        return [types.TextContent(type="text", text=payload["data"])]

    text = json.dumps(payload, ensure_ascii=False, default=str)
    return [types.TextContent(type="text", text=text)]


# ---- entry point -----------------------------------------------------------
async def _main() -> None:
    await _refresh_runtime_state()
    log.info("phantom-mcp v2 ready: %d tools advertised.", len(registry.available()))
    # Restore stdout to the original fd so the MCP stdio transport
    # can write to the real stdout pipe that Jan/LM Studio is reading.
    sys.stdout = _original_stdout
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(_main())
