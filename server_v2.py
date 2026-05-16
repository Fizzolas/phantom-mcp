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
    sizes the token budget. If none are found we retry for up to
    HOST_PROBE_TIMEOUT_S seconds (handles Jan.ai cold-start where the
    API server isn't ready when Phantom is spawned). If still unreachable
    we fall back to 8k context and keep running — all tools that don't
    need the host stay available, and we re-probe on the first list_tools
    call so the budget is corrected the moment the host comes up.

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
import signal
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(ROOT))


# ---- Pre-flight environment check -----------------------------------------
def _validate_environment() -> None:
    """
    Run before any other server initialisation.

    Checks:
      1. Python 3.8+ is required for async/await and several stdlib features.
      2. The server must be run from (or located in) the phantom-mcp directory
         so that relative paths to data/, logs/, and phantom/ are correct.
      3. logs/ and data/ directories must be writable (creates them if absent).
      4. Core package imports are present (mcp, pydantic).

    Exits with a human-readable message on failure — never a raw traceback.
    """
    # 1. Python version
    if sys.version_info < (3, 8):
        sys.stderr.write(
            f"[phantom] ERROR: Python 3.8 or higher is required.\n"
            f"  You are running Python {sys.version.split()[0]}.\n"
            f"  Download a newer version from https://www.python.org/downloads/\n"
        )
        sys.exit(1)

    # 2. Working-directory / install-directory sanity check
    if not (ROOT / "phantom").is_dir():
        sys.stderr.write(
            f"[phantom] ERROR: Cannot find the 'phantom' package directory.\n"
            f"  Expected it at: {ROOT / 'phantom'}\n"
            f"  Make sure you run 'python server_v2.py' from inside the\n"
            f"  phantom-mcp folder, or use the full path:\n"
            f"    python C:\\phantom-mcp\\server_v2.py\n"
        )
        sys.exit(1)

    # 3. Writable directories
    for dirpath in (ROOT / "logs", ROOT / "data"):
        try:
            dirpath.mkdir(parents=True, exist_ok=True)
            probe = dirpath / ".write_probe"
            probe.write_text("ok")
            probe.unlink()
        except OSError as exc:
            sys.stderr.write(
                f"[phantom] ERROR: Cannot write to '{dirpath}'.\n"
                f"  Reason: {exc}\n"
                f"  Check folder permissions or run as a user who owns that directory.\n"
            )
            sys.exit(1)

    # 4. Core imports
    missing = []
    for pkg in ("mcp", "pydantic"):
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        sys.stderr.write(
            f"[phantom] ERROR: Required packages not installed: {', '.join(missing)}\n"
            f"  Run:  pip install -r requirements.txt\n"
        )
        sys.exit(1)


_validate_environment()
# ---------------------------------------------------------------------------


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
_HOST_ONLINE: bool = False
_SHUTDOWN_REQUESTED: bool = False

# How long to keep retrying host probe at boot before giving up (seconds).
HOST_PROBE_TIMEOUT_S = 20.0
HOST_PROBE_RETRY_S = 2.0

# Known host candidates: (label, base_url)
_HOST_CANDIDATES: list[tuple[str, str]] = [
    ("LM Studio",  "http://localhost:1234/v1"),
    ("Jan.ai",     "http://localhost:1337/v1"),
]


async def _try_hosts_once() -> LMStudioProbe | None:
    """
    Make a single pass over all host candidates (and the env-var override).
    Returns the first reachable probe, or None if nothing is up yet.
    """
    override_url = os.environ.get("PHANTOM_HOST_URL", "").strip()
    if override_url:
        result = await probe_lmstudio(base_url=override_url, force=True)
        if result.reachable:
            log.info("host detected via PHANTOM_HOST_URL override: %s (model=%s ctx=%s)",
                     override_url, result.model_id, result.context_length)
            return result
        log.debug("override probe miss: %s → %s", override_url, result.error)

    for label, url in _HOST_CANDIDATES:
        result = await probe_lmstudio(base_url=url, force=True)
        if result.reachable:
            log.info("host detected: %s at %s (model=%s ctx=%s tools=%s)",
                     label, url, result.model_id, result.context_length, result.supports_tools)
            return result
        log.debug("host probe miss: %s (%s) → %s", label, url, result.error)

    return None


async def _detect_host() -> LMStudioProbe:
    """
    Try hosts repeatedly for up to HOST_PROBE_TIMEOUT_S seconds.

    Jan.ai spawns Phantom as a child process before its own API server is
    fully up. Without retries Phantom would probe once, get nothing, and
    drop into offline mode even though Jan is about to be ready.
    """
    global _HOST_ONLINE

    deadline = time.monotonic() + HOST_PROBE_TIMEOUT_S
    attempt = 0

    while time.monotonic() < deadline:
        attempt += 1
        probe = await _try_hosts_once()
        if probe is not None:
            _HOST_ONLINE = True
            return probe

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        wait = min(HOST_PROBE_RETRY_S, remaining)
        log.debug("host not ready yet (attempt %d), retrying in %.1fs (%.0fs left)...",
                  attempt, wait, remaining)
        await asyncio.sleep(wait)

    log.warning(
        "No host reachable after %.0fs (tried LM Studio:1234, Jan.ai:1337%s). "
        "Running in offline mode — tools that don't need a host stay available. "
        "Will re-probe on first tool listing. "
        "Set PHANTOM_HOST_URL env var if your host is on a non-standard port.",
        HOST_PROBE_TIMEOUT_S,
        f", override={os.environ.get('PHANTOM_HOST_URL', '').strip()}" if os.environ.get("PHANTOM_HOST_URL") else "",
    )
    return LMStudioProbe(
        reachable=False,
        context_length=FALLBACK_CONTEXT_LENGTH,
        error="no reachable host found",
    )


async def _refresh_runtime_state(*, silent: bool = False) -> None:
    """Probe capabilities + host; size the budget for the loaded model."""
    global _BUDGET, _LMS_INFO, _HOST_ONLINE

    caps = probe_capabilities()
    registry.set_capabilities(caps)

    host_probe = await _detect_host()
    _HOST_ONLINE = host_probe.reachable
    _LMS_INFO = host_probe.as_dict()
    _BUDGET = TokenBudget(context_length=host_probe.context_length)
    if not silent:
        log.info(
            "boot: capabilities=%s host_reachable=%s model=%s ctx=%s",
            sorted(caps), host_probe.reachable, host_probe.model_id, host_probe.context_length,
        )


# ---- Graceful shutdown -----------------------------------------------------

async def _shutdown(sig_name: str | None = None) -> None:
    """
    Flush in-flight memory writes and close log handlers cleanly.

    Called either by OS signal (SIGINT / SIGTERM) or the finally block in
    _main().  Safe to call more than once — second call is a no-op.
    """
    global _SHUTDOWN_REQUESTED
    if _SHUTDOWN_REQUESTED:
        return
    _SHUTDOWN_REQUESTED = True

    if sig_name:
        log.info("phantom-mcp: received %s — shutting down gracefully...", sig_name)
    else:
        log.info("phantom-mcp: shutting down...")

    try:
        mem = get_memory()
        # PhantomMemory writes are atomic (tmp→rename) and lock-guarded, so
        # the in-memory caches are already flushed on every mutation.  We just
        # need to make sure no write is mid-flight, which the asyncio lock
        # already guarantees.  A short yield is sufficient.
        await asyncio.sleep(0.1)
        log.info("phantom-mcp: memory state clean.")
    except Exception:
        log.exception("phantom-mcp: error during memory flush")

    log.info("phantom-mcp: shutdown complete.")
    # Flush log handlers so the last messages are not lost.
    for h in log.handlers:
        try:
            h.flush()
        except Exception:
            pass


# ---- MCP server ------------------------------------------------------------
app = Server("phantom-mcp")

_list_tools_called = False


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
    global _list_tools_called, _BUDGET, _LMS_INFO, _HOST_ONLINE

    if not _list_tools_called and not _HOST_ONLINE:
        _list_tools_called = True
        log.info("list_tools: host was offline at boot — re-probing now...")
        probe = await _try_hosts_once()
        if probe is not None:
            _HOST_ONLINE = True
            _LMS_INFO = probe.as_dict()
            _BUDGET = TokenBudget(context_length=probe.context_length)
            log.info(
                "late host detect: %s (model=%s ctx=%s tools=%s)",
                probe.base_url, probe.model_id, probe.context_length, probe.supports_tools,
            )
        else:
            log.warning("list_tools re-probe: still no host reachable. Staying in offline mode.")
    else:
        _list_tools_called = True

    available = registry.available()
    log.info("list_tools: %d tools advertised (of %d total)", len(available), len(registry.all()))
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
    # Register OS-level signal handlers for graceful shutdown.
    # Windows does not support SIGTERM in the same way, but SIGINT (Ctrl+C)
    # works on all platforms.  We guard SIGTERM behind a platform check.
    loop = asyncio.get_running_loop()

    def _handle_signal(sig_name: str) -> None:
        log.info("signal %s received", sig_name)
        asyncio.create_task(_shutdown(sig_name))

    try:
        loop.add_signal_handler(signal.SIGINT,  lambda: _handle_signal("SIGINT"))
    except (NotImplementedError, OSError):
        pass  # Windows ProactorEventLoop may not support add_signal_handler
    if hasattr(signal, "SIGTERM"):
        try:
            loop.add_signal_handler(signal.SIGTERM, lambda: _handle_signal("SIGTERM"))
        except (NotImplementedError, OSError):
            pass

    try:
        await _refresh_runtime_state()
        log.info("phantom-mcp v2 ready: %d tools advertised.", len(registry.available()))
        # Restore stdout to the original fd so the MCP stdio transport
        # can write to the real stdout pipe that Jan/LM Studio is reading.
        sys.stdout = _original_stdout
        async with stdio_server() as (read_stream, write_stream):
            await app.run(read_stream, write_stream, app.create_initialization_options())
    finally:
        await _shutdown()


if __name__ == "__main__":
    asyncio.run(_main())
