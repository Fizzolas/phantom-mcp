"""
tools/shell.py — Shell execution

Tools:
  run_cmd            — one-shot CMD command
  run_powershell     — one-shot PowerShell command
  run_python         — run a Python snippet inside the server's own venv
  run_persistent_cmd — CMD session that remembers cwd / env between calls
  reset_persistent_cmd

Output is capped at MAX_OUTPUT chars (8000). Text beyond that is truncated
with a note so the model knows to use memory_chunk_save if it needs the full output.

FIX (Bug 1): Replaced asyncio.get_event_loop() with asyncio.get_running_loop()
  in run_cmd, run_powershell, and run_python. get_event_loop() is deprecated
  in Python 3.10+ and emits DeprecationWarnings; it can also return the wrong
  loop or raise RuntimeError in certain server startup sequences.
  get_running_loop() is the correct call inside a coroutine — it always returns
  the loop that is actively executing the current coroutine, and raises
  RuntimeError immediately if called outside one (fail-fast).

FIX (Bug 2): run_powershell now has an allow_powershell=False safety gate
  equivalent to run_cmd's allow_shell flag. Passing raw strings directly to
  PowerShell via -Command is still an injection vector even without shell=True.

FIX (Bug 3): reset_persistent_cmd now uses _is_proc_alive() instead of the
  stale returncode-only check, consistent with run_persistent_cmd.
"""
from __future__ import annotations

import asyncio
import io
import textwrap
import traceback
from contextlib import redirect_stderr, redirect_stdout
from typing import Any

MAX_OUTPUT = 8_000


def _truncate(text: str, cap: int = MAX_OUTPUT) -> str:
    if len(text) <= cap:
        return text
    half = cap // 2
    return (
        text[:half]
        + f"\n\n... [TRUNCATED — {len(text) - cap} chars omitted] ...\n\n"
        + text[-half:]
    )


# =========================================================
# One-shot CMD
# =========================================================
async def run_cmd(command: str, timeout: int = 30, allow_shell: bool = False) -> dict:
    """
    Run a one-shot CMD command.
    shell=True is gated behind allow_shell=True (injection risk acknowledgement).
    """
    # BUG 1 FIX: get_running_loop() instead of get_event_loop()
    loop = asyncio.get_running_loop()
    if not allow_shell:
        return {
            "error": (
                "run_cmd requires allow_shell=True to execute. "
                "shell=True enables command injection if the input is not "
                "fully controlled. Pass allow_shell=True only when the command "
                "string is constructed entirely by the agent, not from user input."
            ),
            "returncode": -1,
        }
    return await loop.run_in_executor(None, _run_cmd_sync, command, timeout)


def _run_cmd_sync(command: str, timeout: int) -> dict:
    import subprocess
    try:
        result = subprocess.run(
            command,
            shell=True,  # noqa: S602 — caller acknowledged via allow_shell=True
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        return {
            "stdout": _truncate(result.stdout),
            "stderr": _truncate(result.stderr),
            "returncode": result.returncode,
            "shell_warning": "shell=True used — command injection risk acknowledged by caller.",
        }
    except subprocess.TimeoutExpired:
        return {"error": f"Command timed out after {timeout}s", "returncode": -1}
    except Exception as e:
        return {"error": str(e), "returncode": -1}


# =========================================================
# One-shot PowerShell
# =========================================================
async def run_powershell(
    command: str,
    timeout: int = 30,
    allow_powershell: bool = False,
) -> dict:
    """
    Run a one-shot PowerShell command.

    BUG 2 FIX: Although this avoids shell=True by using a list, passing a raw
    string to PowerShell via -Command is still an injection vector if the
    command string contains user-supplied content. Now gated behind
    allow_powershell=True so callers must consciously opt in, identical pattern
    to run_cmd's allow_shell flag.

    allow_powershell=False (default):
        Returns an error dict explaining the risk.
    allow_powershell=True:
        Executes and stamps a 'powershell_warning' key in the response.
    """
    # BUG 1 FIX: get_running_loop() instead of get_event_loop()
    loop = asyncio.get_running_loop()
    if not allow_powershell:
        return {
            "error": (
                "run_powershell requires allow_powershell=True to execute. "
                "Passing raw strings to PowerShell via -Command is an injection "
                "risk if the command contains any user-supplied content. "
                "Pass allow_powershell=True only when the command is fully "
                "agent-constructed."
            ),
            "returncode": -1,
        }
    return await loop.run_in_executor(None, _run_ps_sync, command, timeout)


def _run_ps_sync(command: str, timeout: int) -> dict:
    import subprocess
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        return {
            "stdout": _truncate(result.stdout),
            "stderr": _truncate(result.stderr),
            "returncode": result.returncode,
            "powershell_warning": "Raw -Command string used — injection risk acknowledged by caller.",
        }
    except subprocess.TimeoutExpired:
        return {"error": f"PowerShell timed out after {timeout}s", "returncode": -1}
    except Exception as e:
        return {"error": str(e), "returncode": -1}


# =========================================================
# run_python — execute snippet in-process
# =========================================================
async def run_python(code: str, timeout: int = 30) -> dict:
    """
    Execute a Python snippet inside the Phantom server's own interpreter.

    - Runs in a fresh dict namespace (no cross-call state).
    - stdout and stderr are captured and returned.
    - Exceptions are caught and returned as stderr.
    - Hard timeout via asyncio.wait_for wrapping a thread executor.
    """
    # BUG 1 FIX: get_running_loop() instead of get_event_loop()
    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(None, _run_python_sync, code),
            timeout=timeout,
        )
        return result
    except asyncio.TimeoutError:
        return {"error": f"Python snippet timed out after {timeout}s", "returncode": -1}


def _run_python_sync(code: str) -> dict:
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    ns: dict[str, Any] = {}
    returncode = 0

    try:
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            compiled = compile(textwrap.dedent(code), "<phantom_snippet>", "exec")
            exec(compiled, ns)  # noqa: S102
    except SystemExit as e:
        returncode = e.code if isinstance(e.code, int) else 1
    except Exception:
        stderr_buf.write(traceback.format_exc())
        returncode = 1

    return {
        "stdout": _truncate(stdout_buf.getvalue()),
        "stderr": _truncate(stderr_buf.getvalue()),
        "returncode": returncode,
    }


# =========================================================
# Persistent CMD session
# =========================================================
_PERSIST_PROC = None
_PERSIST_LOCK: asyncio.Lock | None = None


def _get_persist_lock() -> asyncio.Lock:
    """Return (and lazily create) the persistent CMD lock.
    Must only be called from inside a running async context."""
    global _PERSIST_LOCK
    if _PERSIST_LOCK is None:
        _PERSIST_LOCK = asyncio.Lock()
    return _PERSIST_LOCK


def _is_proc_alive(proc) -> bool:
    """
    Liveness check for the persistent CMD subprocess.
    Catches zombie processes whose returncode is still None but whose
    stdin pipe is already broken (stale handle).
    """
    if proc is None:
        return False
    if proc.returncode is not None:
        return False
    try:
        proc.stdin.write(b"")  # zero-byte probe; raises if pipe is dead
        return True
    except (BrokenPipeError, OSError, AttributeError):
        return False


async def run_persistent_cmd(command: str, timeout: int = 30) -> dict:
    global _PERSIST_PROC
    async with _get_persist_lock():
        if not _is_proc_alive(_PERSIST_PROC):
            if _PERSIST_PROC is not None:
                try:
                    _PERSIST_PROC.kill()
                except Exception:
                    pass
            _PERSIST_PROC = await asyncio.create_subprocess_shell(
                "cmd",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

        sentinel = "__PHANTOM_DONE__"
        full_cmd = f"{command} && echo {sentinel}\n"
        _PERSIST_PROC.stdin.write(full_cmd.encode("utf-8", errors="replace"))
        await _PERSIST_PROC.stdin.drain()

        output_lines: list[str] = []
        try:
            while True:
                line_bytes = await asyncio.wait_for(
                    _PERSIST_PROC.stdout.readline(), timeout=timeout
                )
                line = line_bytes.decode("utf-8", errors="replace")
                if sentinel in line:
                    break
                output_lines.append(line)
        except asyncio.TimeoutError:
            return {
                "error": (
                    f"Persistent CMD timed out after {timeout}s — "
                    f"hung command: {command!r}. "
                    "Call reset_persistent_cmd() to clear the session."
                ),
                "returncode": -1,
            }

        output = _truncate("".join(output_lines))
        return {"stdout": output, "stderr": "", "returncode": 0}


async def reset_persistent_cmd() -> dict:
    global _PERSIST_PROC
    async with _get_persist_lock():
        # BUG 3 FIX: use _is_proc_alive() instead of returncode-only check,
        # consistent with run_persistent_cmd and catching zombie processes.
        if _is_proc_alive(_PERSIST_PROC):
            try:
                _PERSIST_PROC.kill()
            except Exception:
                pass
        _PERSIST_PROC = None
    return {"ok": True, "message": "Persistent CMD session reset"}
