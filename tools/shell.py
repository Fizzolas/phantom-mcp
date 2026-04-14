"""
Shell execution — CMD, PowerShell, and a persistent session.
The persistent session remembers cwd, env vars, and chained state.
"""
import asyncio, os
from pathlib import Path

_persistent_session = {
    "cwd": os.path.expanduser("~"),
    "env": os.environ.copy(),
}

async def run_cmd(command: str, timeout: int = 30) -> dict:
    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        shell=True,
        cwd=_persistent_session["cwd"],
        env=_persistent_session["env"],
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return {"status": "timeout", "stdout": "", "stderr": f"Timed out after {timeout}s"}
    return {
        "status": "ok" if proc.returncode == 0 else "error",
        "returncode": proc.returncode,
        "stdout": stdout.decode(errors="replace").strip(),
        "stderr": stderr.decode(errors="replace").strip(),
    }

async def run_powershell(command: str, timeout: int = 30) -> dict:
    ps_cmd = ["powershell.exe", "-NoProfile", "-NonInteractive",
              "-ExecutionPolicy", "Bypass", "-Command", command]
    proc = await asyncio.create_subprocess_exec(
        *ps_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=_persistent_session["cwd"],
        env=_persistent_session["env"],
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return {"status": "timeout", "stdout": "", "stderr": f"Timed out after {timeout}s"}
    return {
        "status": "ok" if proc.returncode == 0 else "error",
        "returncode": proc.returncode,
        "stdout": stdout.decode(errors="replace").strip(),
        "stderr": stderr.decode(errors="replace").strip(),
    }

async def run_persistent_cmd(command: str) -> dict:
    stripped = command.strip()
    if stripped.lower().startswith("cd "):
        target = stripped[3:].strip().strip('"')
        new_path = Path(_persistent_session["cwd"]) / target
        new_path = new_path.resolve()
        if new_path.is_dir():
            _persistent_session["cwd"] = str(new_path)
            return {"status": "ok", "cwd": str(new_path)}
        return {"status": "error", "stderr": f"Directory not found: {new_path}"}
    result = await run_cmd(command, timeout=60)
    result["session_cwd"] = _persistent_session["cwd"]
    return result
