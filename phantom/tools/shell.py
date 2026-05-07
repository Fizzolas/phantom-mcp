"""
phantom.tools.shell — run shell commands, PowerShell, and Python snippets.

These wrap legacy tools/shell.py. Outputs already pass through that
module's _truncate(). The phantom executor adds budget-aware truncation
on top so outputs never explode the LM Studio context.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from phantom.contracts import ok, fail
from phantom.tools._base import tool


class RunCmdInput(BaseModel):
    command: str = Field(..., min_length=1, description="Shell command line.")
    timeout: int = Field(30, ge=1, le=600)

    model_config = ConfigDict(extra="forbid")


@tool(
    "shell_cmd",
    category="shell",
    schema=RunCmdInput,
    timeout_s=620.0,
)
async def shell_cmd(command: str, timeout: int = 30) -> dict:
    """
    Run a shell command (cmd.exe on Windows, /bin/sh elsewhere).

    Returns {stdout, stderr, returncode}. Output is truncated if very
    large. Use shell_python for inline Python and shell_powershell for
    PowerShell-specific cmdlets.
    """
    from tools.shell import run_cmd as legacy
    result = await legacy(command, timeout=timeout)
    if isinstance(result, dict) and result.get("error") and result.get("returncode") == -1:
        return fail(
            result["error"],
            hint="Increase timeout, simplify the command, or check it exists on PATH.",
            category="external_error",
        )
    return ok(result)


class RunPSInput(BaseModel):
    script: str = Field(..., min_length=1, description="PowerShell script body.")
    timeout: int = Field(60, ge=1, le=600)

    model_config = ConfigDict(extra="forbid")


@tool(
    "shell_powershell",
    category="shell",
    schema=RunPSInput,
    needs=("os:windows",),
    timeout_s=620.0,
)
async def shell_powershell(script: str, timeout: int = 60) -> dict:
    """
    Run a PowerShell script (Windows only). Returns {stdout, stderr, returncode}.
    """
    from tools.shell import run_powershell as legacy
    result = await legacy(script, timeout=timeout)
    if isinstance(result, dict) and result.get("error") and result.get("returncode") == -1:
        return fail(result["error"], category="external_error")
    return ok(result)


class RunPythonInput(BaseModel):
    code: str = Field(..., min_length=1, description="Python source. No persistent state across calls.")
    timeout: int = Field(60, ge=1, le=600)

    model_config = ConfigDict(extra="forbid")


@tool(
    "shell_python",
    category="shell",
    schema=RunPythonInput,
    timeout_s=620.0,
)
async def shell_python(code: str, timeout: int = 60) -> dict:
    """
    Execute a Python snippet in a fresh namespace inside the server.

    Use for quick computations, JSON parsing, or one-off scripted logic.
    Each call gets a fresh interpreter context — variables do not persist.
    """
    from tools.shell import run_python as legacy
    result = await legacy(code, timeout=timeout)
    if isinstance(result, dict) and result.get("error") and result.get("returncode") == -1:
        return fail(result["error"], category="server_error")
    return ok(result)
