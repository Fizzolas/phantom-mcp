"""
phantom.tools.pc_info — live PC hardware snapshot.

Wraps the existing tools/pc_info.get_pc_snapshot. This is the simplest
possible migration: one async function, no schema fields, no dep-gating.

What changes vs the legacy call:
  * Returns via the ToolResult envelope (ok + data + meta).
  * Truncation/timing handled by the executor, not the tool.
  * No hardcoded "Intel Core i7-13620H" branding leaks into envelope meta.
    (The underlying snapshot still reports it — to be fixed in a later PR.)
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from phantom.tools._base import tool


class SystemInfoInput(BaseModel):
    """No inputs; this tool is a pure read."""

    model_config = ConfigDict(extra="forbid")


@tool(
    "system_info",
    category="system",
    schema=SystemInfoInput,
    timeout_s=5.0,
)
async def system_info() -> dict:
    """
    Return a snapshot of the host PC: CPU load, RAM/swap, disks, GPU, network.

    Use this when the user asks about "system status", "how hot is my GPU",
    "disk space", or similar hardware questions. The snapshot is live, not
    cached — each call re-reads /proc (Linux), WMI (Windows), or sysctl (macOS).
    """
    # Import lazily so a failure in psutil/GPUtil only skips this tool, not
    # the entire tool-module import.
    from tools.pc_info import get_pc_snapshot

    return await get_pc_snapshot()
