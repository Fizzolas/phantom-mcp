"""
phantom.tools.shell — execute shell / PowerShell / Python snippets.

The legacy module has four entry points (run_cmd, run_powershell, run_python,
run_persistent_cmd). PR 3 merges them into one dispatcher, `shell_exec`,
with a `language` enum. Fewer tools = less context noise. The persistent
variant is intentionally not exposed — it's an advanced mode that the
planner can reintroduce later if needed.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from phantom.contracts import fail, ok
from phantom.tools._base import tool


Language = Literal["shell", "powershell", "python"]


class ShellExecInput(BaseModel):
    language: Language = Field(..., description="Which interpreter to use.")
    command: str = Field(..., min_length=1, description="Command or code to execute.")
    timeout_s: int = Field(30, ge=1, le=300, description="Hard timeout.")

    model_config = ConfigDict(extra="forbid")


@tool("shell_exec", category="system", schema=ShellExecInput, timeout_s=310.0)
async def shell_exec(language: str, command: str, timeout_s: int = 30) -> dict:
    """
    Run a command in the host's shell, PowerShell, or an inline Python.

    `language` selects the interpreter:
      * shell       — the platform's default shell (bash/zsh on Unix, cmd on Windows).
      * powershell  — Windows PowerShell. On non-Windows hosts this fails cleanly.
      * python      — a fresh Python subprocess; use for scripts that need libraries.

    Returns `{stdout, stderr, returncode, elapsed_s}`. Non-zero exit codes
    are NOT treated as tool failures — the model decides how to react.
    """
    from tools.shell import run_cmd, run_powershell, run_python

    fn = {"shell": run_cmd, "powershell": run_powershell, "python": run_python}[language]
    result = await fn(command=command, timeout=timeout_s)
    if isinstance(result, dict) and result.get("error") and "returncode" not in result:
        return fail(
            str(result["error"]),
            hint="Check the command syntax, timeout, or interpreter availability.",
            category="external_error",
        )
    return ok(result)
