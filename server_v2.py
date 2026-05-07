"""
phantom-mcp server v2 — slim entry point built on the phantom registry.

What changed vs server.py:
  * No 1500-line dispatch table. Tools are imported once; the
    ToolRegistry routes calls and validates arguments via pydantic.
  * Tool surface is shaped at boot by the capability probe — tools
    whose deps are missing don't appear in list_tools, so the LM Studio
    model never tries to call them.
  * Every tool result is shaped as ToolResult (ok/data/error/hint/meta).
    Outputs that exceed the per-call token budget are truncated with a
    sentinel rather than silently dropping data.
  * Every tool call is recorded in PhantomMemory traces (tool name, args
    summary, ok, error, latency). memory_learn_from_traces distills
    repeated failures into lessons the model can read at boot.
  * LM Studio is probed at boot to size the token budget for the loaded
    model. If LM Studio is unreachable we default to 8k context.

Run:
    python server_v2.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
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
print("phantom-mcp v2 starting...", flush=True)

# ---- phantom internals -----------------------------------------------------
from phantom.contracts import ToolResult, fail
from phantom.runtime.budget import TokenBudget
from phantom.runtime.capabilities import probe_capabilities
from phantom.runtime.lmstudio import probe_lmstudio
import phantom.tools  # registers everything via @tool decorators
from phantom.tools._base import registry
from phantom.tools.memory import get_memory, set_memory
from phantom.memory.store import PhantomMemory


# ---- boot config ------------------------------------------------------------
DATA_DIR = ROOT / "data" / "phantom_memory"
set_memory(PhantomMemory(DATA_DIR))


_BUDGET: TokenBudget | None = None
_LMS_INFO: dict | None = None


async def _refresh_runtime_state() -> None:
    """Probe capabilities + LM Studio; size the budget for the loaded model."""
    global _BUDGET, _LMS_INFO

    caps = probe_capabilities()
    registry.set_capabilities(caps)

    probe = await probe_lmstudio()
    _LMS_INFO = probe.as_dict()
    _BUDGET = TokenBudget(context_length=probe.context_length)
    log.info(
        "boot: capabilities=%s lms_reachable=%s model=%s ctx=%s",
        sorted(caps), probe.reachable, probe.model_id, probe.context_length,
    )


# ---- MCP server ------------------------------------------------------------
app = Server("phantom-mcp")
_tools_list_logged = False


def _spec_to_mcp_tool(spec) -> types.Tool:
    """Translate a registry ToolSpec into the MCP types.Tool the client sees."""
    schema = spec.json_schema()
    # MCP wants 'properties' present even on no-arg tools.
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    return types.Tool(
        name=spec.name,
        description=spec.description or "(no description provided)",
        inputSchema=schema,
    )


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    global _tools_list_logged
    available = registry.available()
    if not _tools_list_logged:
        log.info("list_tools: %d tools advertised (of %d total)", len(available), len(registry.all()))
        _tools_list_logged = True
    return [_spec_to_mcp_tool(s) for s in available]


@app.call_tool()
async def call_tool(name: str, arguments: dict | None = None) -> list[types.TextContent]:
    args = arguments or {}
    started = time.monotonic()
    log.debug("call: %s args=%s", name, list(args.keys()))

    result: ToolResult = await registry.call(name, args)

    # Record a trace (best-effort; never let trace IO break the call).
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

    # Apply context-window budget — never blow LM Studio's max tokens.
    if _BUDGET is not None:
        try:
            data, was_truncated = _BUDGET.fit_any(payload.get("data"))
            payload["data"] = data
            if was_truncated:
                payload.setdefault("meta", {})["truncated"] = True
        except Exception:
            log.exception("budget.fit_any failed for %s", name)

    text = json.dumps(payload, ensure_ascii=False, default=str)
    return [types.TextContent(type="text", text=text)]


# ---- entry point -----------------------------------------------------------
async def _main() -> None:
    await _refresh_runtime_state()
    print(f"phantom-mcp v2 ready: {len(registry.available())} tools advertised.", flush=True)
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(_main())
