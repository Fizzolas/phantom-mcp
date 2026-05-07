"""
phantom.tools.processes — list/find/kill/launch processes.

Wraps tools/process_ops.py. Kill is rate-protected by the legacy module
(blocks Windows kernel PIDs and well-known system processes).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from phantom.contracts import ok, fail
from phantom.tools._base import tool


SortBy = Literal["ram", "cpu", "name", "pid"]


class ListProcessesInput(BaseModel):
    sort_by: SortBy = Field("ram")
    limit: int = Field(50, ge=1, le=200)

    model_config = ConfigDict(extra="forbid")


@tool("process_list", category="processes", schema=ListProcessesInput, timeout_s=15.0)
async def process_list(sort_by: str = "ram", limit: int = 50) -> dict:
    """
    List running processes with PID, name, RAM, CPU%.
    Sort by ram (default), cpu, name, or pid.
    """
    from tools.process_ops import list_processes as legacy
    return ok({"processes": await legacy(sort_by=sort_by, limit=limit)})


class FindProcessInput(BaseModel):
    name: str = Field(..., min_length=1, description="Substring of the process name.")

    model_config = ConfigDict(extra="forbid")


@tool("process_find", category="processes", schema=FindProcessInput, timeout_s=10.0)
async def process_find(name: str) -> dict:
    """
    Find processes whose name contains `name`.
    """
    from tools.process_ops import find_process as legacy
    return ok({"matches": await legacy(name)})


class KillProcessInput(BaseModel):
    pid: int = Field(..., ge=1)
    force: bool = Field(False, description="Force-kill (SIGKILL/TerminateProcess) — required for protected names.")

    model_config = ConfigDict(extra="forbid")


@tool("process_kill", category="processes", schema=KillProcessInput, timeout_s=10.0)
async def process_kill(pid: int, force: bool = False) -> dict:
    """
    Terminate a process by PID. Kernel/system PIDs are blocked. Critical
    process names require force=True to confirm.
    """
    from tools.process_ops import kill_process as legacy
    msg = await legacy(pid, force=force)
    if msg.startswith("BLOCKED") or msg.startswith("ERROR"):
        return fail(msg, category="client_error")
    return ok({"message": msg, "pid": pid})


class LaunchAppInput(BaseModel):
    target: str = Field(..., min_length=1, description="Path/command to launch (e.g. 'notepad' or 'C:\\\\path\\\\app.exe').")
    wait: bool = Field(False)
    timeout: int = Field(10, ge=1, le=300)

    model_config = ConfigDict(extra="forbid")


@tool("process_launch", category="processes", schema=LaunchAppInput, timeout_s=310.0)
async def process_launch(target: str, wait: bool = False, timeout: int = 10) -> dict:
    """
    Launch an application or shell command in a detached process.
    """
    from tools.process_ops import launch_app as legacy
    msg = await legacy(target, wait=wait, timeout=timeout)
    if msg.startswith("ERROR"):
        return fail(msg, category="external_error")
    return ok({"message": msg})
