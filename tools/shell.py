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
"""
from __future__ import annotations

import asyncio
import io
import sys
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
async def run_cmd(command: str, timeout: int = 30) -> dict:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _run_cmd_sync, command, timeout)


def _run_cmd_sync(command: str, timeout: int) -> dict:
    import subprocess
    try:
        result = subprocess.run(
            command,
            shell=True,
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
        }
    except subprocess.TimeoutExpired:
        return {"error": f"Command timed out after {timeout}s", "returncode": -1}
    except Exception as e:
        return {"error": str(e), "returncode": -1}


# =========================================================
# One-shot PowerShell
# =========================================================
async def run_powershell(command: str, timeout: int = 30) -> dict:
    loop = asyncio.get_event_loop()
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
    loop = asyncio.get_event_loop()
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
_PERSIST_LOCK = asyncio.Lock()


async def run_persistent_cmd(command: str, timeout: int = 30) -> dict:
    global _PERSIST_PROC
    async with _PERSIST_LOCK:
        if _PERSIST_PROC is None or _PERSIST_PROC.returncode is not None:
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
            return {"error": f"Persistent CMD timed out after {timeout}s", "returncode": -1}

        output = _truncate("".join(output_lines))
        return {"stdout": output, "stderr": "", "returncode": 0}


async def reset_persistent_cmd() -> dict:
    global _PERSIST_PROC
    async with _PERSIST_LOCK:
        if _PERSIST_PROC and _PERSIST_PROC.returncode is None:
            try:
                _PERSIST_PROC.kill()
            except Exception:
                pass
        _PERSIST_PROC = None
    return {"ok": True, "message": "Persistent CMD session reset"}
