"""
phantom.tools.notify — desktop toast notifications.

Demonstrates:
  * Cross-platform capability-gated behavior. The legacy tool is Windows-only
    (win10toast + BurntToast). We declare needs=("desktop",) so the registry
    can hide this tool when the host has no desktop session (headless Linux,
    SSH sessions). LM Studio only runs on desktop-class machines, matching
    the user's "only instances in which LM Studio would be used" guidance.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from phantom.tools._base import tool


class NotifyInput(BaseModel):
    title: str = Field(..., description="Short heading for the toast.")
    message: str = Field(..., description="Body text.")
    duration_s: int = Field(5, ge=1, le=60, description="Seconds to display.")

    model_config = ConfigDict(extra="forbid")


@tool(
    "notify_user",
    category="notify",
    schema=NotifyInput,
    needs=("desktop",),
    timeout_s=10.0,
)
async def notify_user(title: str, message: str, duration_s: int = 5) -> dict:
    """
    Show a desktop toast/notification to the user.

    Use this ONLY when the user needs to be alerted to something
    time-sensitive (goal complete, blocked state, long task finished).
    Do NOT use for routine progress updates — that noise is worse than
    useful.

    Returns `{backend, title}` describing which notification path succeeded.
    """
    from tools.notify import notify_user as legacy_notify

    backend = await legacy_notify(title, message, duration_s)
    return {"backend": backend, "title": title}
