"""
Shell tools — CMD, PowerShell, and a persistent CMD session.
Output is capped at MAX_OUTPUT_CHARS to prevent flooding the LLM context.

FIX: reset_persistent_cmd added so the agent can recover from an unknown cwd.
"""
import asyncio
import subprocess
import os
from typing import Optional

MAX_OUTPUT_CHARS = 8000


def _truncate(text: str, label: str = "output") -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    half = MAX_OUTPUT_CHARS // 2
    return (
        text[:half]
        + f"\n\n... [{label} truncated — {len(text)} chars total, showing first+last {half}] ...\n\n"
        + text[-half:]
    )


async def run_cmd(command: str, timeout: int = 30) -> dict:
    """Run a single CMD command. Returns stdout, stderr, returncode."""
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            shell=True,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return {
            "stdout": _truncate(stdout.decode(errors="replace"), "stdout"),
            "stderr": _truncate(stderr.decode(errors="replace"), "stderr"),
            "returncode": proc.returncode,
        }
    except asyncio.TimeoutError:
        return {"error": f"Command timed out after {timeout}s", "returncode": -1}
    except Exception as e:
        return {"error": str(e), "returncode": -1}


async def run_powershell(command: str, timeout: int = 30) -> dict:
    """Run a PowerShell command or multi-line script block."""
    ps_exe = "powershell.exe"
    args = [ps_exe, "-NoProfile", "-NonInteractive", "-Command", command]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return {
            "stdout": _truncate(stdout.decode(errors="replace"), "stdout"),
            "stderr": _truncate(stderr.decode(errors="replace"), "stderr"),
            "returncode": proc.returncode,
        }
    except asyncio.TimeoutError:
        return {"error": f"PowerShell timed out after {timeout}s", "returncode": -1}
    except Exception as e:
        return {"error": str(e), "returncode": -1}


# --- Persistent CMD session ---
_persistent_proc: Optional[asyncio.subprocess.Process] = None
_persistent_lock = asyncio.Lock()
SENTINEL = "__PHANTOM_CMD_DONE__"


async def _get_persistent_proc() -> asyncio.subprocess.Process:
    global _persistent_proc
    if _persistent_proc is None or _persistent_proc.returncode is not None:
        _persistent_proc = await asyncio.create_subprocess_shell(
            "cmd.exe /Q",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    return _persistent_proc


async def run_persistent_cmd(command: str, timeout: int = 30) -> dict:
    """Run CMD in a persistent session that retains cwd and env between calls."""
    async with _persistent_lock:
        try:
            proc = await _get_persistent_proc()
            full_cmd = f"{command}\r\necho {SENTINEL}\r\n"
            proc.stdin.write(full_cmd.encode())
            await proc.stdin.drain()

            lines = []
            while True:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
                decoded = line.decode(errors="replace").rstrip()
                if SENTINEL in decoded:
                    break
                lines.append(decoded)

            output = _truncate("\n".join(lines), "output")
            return {"stdout": output, "returncode": 0}
        except asyncio.TimeoutError:
            _persistent_proc = None  # reset on timeout so next call gets fresh session
            return {"error": f"Persistent CMD timed out after {timeout}s", "returncode": -1}
        except Exception as e:
            _persistent_proc = None
            return {"error": str(e), "returncode": -1}


async def reset_persistent_cmd() -> dict:
    """
    Kill the persistent CMD session and start a fresh one.
    Resets cwd to whatever directory cmd.exe opens in by default.
    Use when the session is stuck, in an unknown directory, or after a crash.
    """
    global _persistent_proc
    async with _persistent_lock:
        if _persistent_proc is not None and _persistent_proc.returncode is None:
            try:
                _persistent_proc.kill()
                await _persistent_proc.wait()
            except Exception:
                pass
        _persistent_proc = None
    return {"ok": True, "message": "Persistent CMD session reset. Next run_persistent_cmd will start fresh."}
