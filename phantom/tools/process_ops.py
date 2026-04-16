"""
phantom.tools.process_ops — list, find, kill, launch processes.

Legacy had four tools; we keep them as four because their semantics are
genuinely different. What changes is the schemas and envelope.

Fixes vs legacy:
  * kill_process now accepts int PID *or* str name, explicit via a typed
    field, rather than the server schema claiming string but the impl
    only handling int.
  * launch_app declares `needs=("desktop",)` so headless hosts don't see it.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from phantom.contracts import fail, ok
from phantom.tools._base import tool


SortBy = Literal["ram", "cpu", "pid", "name"]


class ListProcessesInput(BaseModel):
    sort_by: SortBy = Field("ram")
    limit: int = Field(50, ge=1, le=500)
    model_config = ConfigDict(extra="forbid")


class FindProcessInput(BaseModel):
    name: str = Field(..., min_length=1, description="Substring match against process names.")
    model_config = ConfigDict(extra="forbid")


class KillProcessInput(BaseModel):
    target: int | str = Field(..., description="PID (int) or process-name substring (str).")
    force: bool = Field(False, description="Use SIGKILL / --force; last resort.")
    model_config = ConfigDict(extra="forbid")


class LaunchAppInput(BaseModel):
    target: str = Field(..., min_length=1, description="Executable path or app name on PATH.")
    wait: bool = Field(False, description="Block until the process exits?")
    timeout_s: int = Field(10, ge=1, le=300)
    model_config = ConfigDict(extra="forbid")


@tool("list_processes", category="system", schema=ListProcessesInput, timeout_s=10.0)
async def list_processes(sort_by: str = "ram", limit: int = 50) -> dict:
    """List running processes sorted by RAM / CPU / PID / name."""
    from tools.process_ops import list_processes as legacy

    return ok({"processes": await legacy(sort_by=sort_by, limit=limit)})


@tool("find_process", category="system", schema=FindProcessInput, timeout_s=10.0)
async def find_process(name: str) -> dict:
    """Find running processes whose command line contains `name`."""
    from tools.process_ops import find_process as legacy

    return ok({"matches": await legacy(name=name)})


@tool("kill_process", category="system", schema=KillProcessInput, timeout_s=15.0)
async def kill_process(target: int | str, force: bool = False) -> dict:
    """
    Terminate a process. `target` is either a PID (int) or a name
    substring (str) — if a name, the first match is killed.
    """
    from tools.process_ops import find_process as legacy_find, kill_process as legacy_kill

    if isinstance(target, str):
        matches = await legacy_find(name=target)
        if not matches:
            return fail(
                f"No process matches {target!r}.",
                hint="Call list_processes or find_process to confirm.",
                category="client_error",
            )
        pid = matches[0].get("pid") or matches[0].get("PID")
        if pid is None:
            return fail("Matched process has no PID in snapshot.", category="server_error")
    else:
        pid = int(target)

    result = await legacy_kill(pid=pid, force=force)
    return ok({"pid": pid, "result": result})


@tool("launch_app", category="system", schema=LaunchAppInput, needs=("desktop",), timeout_s=310.0)
async def launch_app(target: str, wait: bool = False, timeout_s: int = 10) -> dict:
    """
    Start a desktop application by path or name. Returns launch metadata.
    Cross-platform: uses Popen with platform-appropriate detach flags.
    """
    from tools.process_ops import launch_app as legacy

    result = await legacy(target=target, wait=wait, timeout=timeout_s)
    return ok(result)
