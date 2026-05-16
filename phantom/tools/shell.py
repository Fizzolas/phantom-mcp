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


def _normalize_cmd_command(command: str) -> tuple[str, str | None]:
    """
    Normalize common model mistakes before passing to cmd.exe.

    Returns (normalized_command, warning_or_none).
    """
    stripped = command.strip()

    # Detect PowerShell-only cmdlets and warn instead of silently failing
    ps_only = ("New-Item", "Get-ChildItem", "Set-Location", "Copy-Item",
               "Remove-Item", "Write-Host", "Write-Output", "-Path",
               "Invoke-WebRequest", "Select-Object", "Where-Object")
    for ps_cmd in ps_only:
        if ps_cmd.lower() in stripped.lower():
            return command, (
                f"Command appears to contain PowerShell syntax ({ps_cmd!r}). "
                "Use shell_powershell instead of shell_cmd for PowerShell cmdlets."
            )

    # Unix-isms: touch -> type nul >
    if stripped.lower().startswith("touch "):
        rest = stripped[6:].strip()
        return f"type nul > {rest}", "Converted Unix 'touch' to cmd.exe equivalent."

    # Unix-isms: ls -> dir
    if stripped.lower() in ("ls", "ls ") or stripped.lower().startswith("ls "):
        return "dir " + stripped[3:], "Converted Unix 'ls' to 'dir'."

    # Unix-isms: cat -> type
    if stripped.lower().startswith("cat "):
        return "type " + stripped[4:], "Converted Unix 'cat' to 'type'."

    # mkdir with only forward slashes -> convert to backslashes
    # (cmd.exe handles both but some Windows versions are picky)
    if stripped.lower().startswith(("mkdir ", "md ")):
        cmd_part, _, path_part = stripped.partition(" ")
        # Replace forward slashes with backslashes in the path
        path_part = path_part.replace("/", "\\")
        return f"{cmd_part} {path_part}", None

    return command, None


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
    Run a command in cmd.exe (Windows) or /bin/sh (Linux/Mac).

    WINDOWS CMD.EXE SYNTAX RULES — read before using:
    -------------------------------------------------------
    CORRECT examples (cmd.exe native):
        mkdir C:\\Users\\Name\\Desktop\\MyFolder
        md "C:\\Users\\Name\\Desktop\\My Folder"   <- quotes OK for spaces
        dir C:\\Users\\Name\\Desktop
        del C:\\path\\to\\file.txt
        copy src.txt dst.txt
        echo hello
        type filename.txt
        set VAR=value

    WRONG — these fail in cmd.exe, use shell_powershell instead:
        New-Item -ItemType Directory -Path ...   <- PowerShell only
        Get-ChildItem                            <- PowerShell only
        ls, cat, touch, grep                     <- Unix/PowerShell only
        Invoke-WebRequest                        <- PowerShell only

    FOLDER CREATION on Windows:
        mkdir C:\\Users\\sekri\\Desktop\\MyFolder     <- no quotes needed
        md "C:\\path with spaces\\NewFolder"           <- quotes for spaces

    Returns {stdout, stderr, returncode}. returncode 0 = success.
    If you get 'syntax of the command is incorrect', switch to shell_powershell.
    """
    normalized, warning = _normalize_cmd_command(command)

    # If normalization detected a PowerShell command, return a helpful error
    # immediately rather than letting cmd.exe produce a confusing error.
    if warning and "shell_powershell" in warning:
        return fail(
            warning,
            hint="Use shell_powershell for PowerShell cmdlets like New-Item, Get-ChildItem, etc.",
            category="usage_error",
        )

    from tools.shell import run_cmd as legacy
    result = await legacy(normalized, timeout=timeout)

    # Attach normalization warning if we changed the command
    if warning and isinstance(result, dict):
        result["normalization_note"] = warning

    if isinstance(result, dict) and result.get("error") and result.get("returncode") == -1:
        return fail(
            result["error"],
            hint="Increase timeout, simplify the command, or check it exists on PATH.",
            category="external_error",
        )

    # Helpful hint when cmd.exe syntax error occurs
    if isinstance(result, dict) and result.get("returncode", 0) == 1:
        stderr = result.get("stderr", "").lower()
        if "syntax" in stderr or "unexpected" in stderr:
            result["hint"] = (
                "cmd.exe reported a syntax error. "
                "Try shell_powershell for PowerShell-style commands, "
                "or check that paths use backslashes (not forward slashes). "
                "Example: mkdir C:\\\\Users\\\\Name\\\\Desktop\\\\Folder"
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

    POWERSHELL SYNTAX EXAMPLES:
    -------------------------------------------------------
    Create a folder:
        New-Item -ItemType Directory -Path "C:\\Users\\Name\\Desktop\\MyFolder" -Force

    List files:
        Get-ChildItem "C:\\Users\\Name\\Desktop"

    Copy file:
        Copy-Item "src.txt" "dst.txt"

    Delete file:
        Remove-Item "C:\\path\\file.txt"

    Download file:
        Invoke-WebRequest -Uri "https://example.com/file" -OutFile "file.txt"

    Prefer shell_powershell over shell_cmd for anything involving:
    New-Item, Get-ChildItem, Copy-Item, Remove-Item, Invoke-WebRequest,
    Set-Location, Write-Host, Select-Object, Where-Object, ForEach-Object.
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
