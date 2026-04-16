"""
phantom.tools.window_ops — enumerate and manipulate OS windows.

Legacy had 9 separate tools (list, focus, get_active, minimize, maximize,
restore, get_rect, resize, move). PR 3 keeps them because they all do
different things, but collapses minimize/maximize/restore into one
`window_state` tool with an enum.

Result: 7 tools down from 9, all gated behind needs=("desktop",).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from phantom.contracts import fail, ok
from phantom.tools._base import tool


WindowState = Literal["minimize", "maximize", "restore"]


class NoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WindowTitleInput(BaseModel):
    title: str = Field(..., min_length=1, description="Window title (case-insensitive substring).")
    strict: bool = Field(False, description="If true, require exact case-insensitive match.")
    model_config = ConfigDict(extra="forbid")


class WindowStateInput(BaseModel):
    title: str = Field(..., min_length=1)
    state: WindowState = Field(..., description="Desired state.")
    model_config = ConfigDict(extra="forbid")


class WindowResizeInput(BaseModel):
    title: str = Field(..., min_length=1)
    width: int = Field(..., gt=0)
    height: int = Field(..., gt=0)
    model_config = ConfigDict(extra="forbid")


class WindowMoveInput(BaseModel):
    title: str = Field(..., min_length=1)
    x: int = Field(..., ge=-10000, le=10000)
    y: int = Field(..., ge=-10000, le=10000)
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------


@tool("list_windows", category="ui", schema=NoArgs, needs=("desktop",), timeout_s=5.0)
async def list_windows() -> dict:
    """List all visible top-level windows with title, pid, and position."""
    from tools.window_ops import list_windows as legacy

    return ok({"windows": await legacy()})


@tool("focus_window", category="ui", schema=WindowTitleInput, needs=("desktop",), timeout_s=10.0)
async def focus_window(title: str, strict: bool = False) -> dict:
    """
    Bring a window to the foreground. Case-insensitive substring match by
    default; set strict=True for exact-title match.
    """
    from tools.window_ops import focus_window as legacy

    result = await legacy(title=title, strict=strict)
    if isinstance(result, dict) and result.get("error"):
        return fail(result["error"], category="client_error", available=result.get("available_titles"))
    return ok(result)


@tool("active_window", category="ui", schema=NoArgs, needs=("desktop",), timeout_s=5.0)
def active_window() -> dict:
    """Return the currently focused window's title and pid."""
    from tools.window_ops import get_active_window as legacy

    return ok(legacy())


@tool("window_state", category="ui", schema=WindowStateInput, needs=("desktop",), timeout_s=10.0)
async def window_state(title: str, state: str) -> dict:
    """
    Change a window's state: minimize, maximize, or restore. Collapses the
    legacy minimize/maximize/restore tool trio.
    """
    from tools.window_ops import (
        minimize_window as legacy_min,
        maximize_window as legacy_max,
        restore_window as legacy_restore,
    )

    fn = {"minimize": legacy_min, "maximize": legacy_max, "restore": legacy_restore}[state]
    return ok({"result": await fn(title=title)})


@tool("window_rect", category="ui", schema=WindowTitleInput, needs=("desktop",), timeout_s=5.0)
async def window_rect(title: str, strict: bool = False) -> dict:
    """Return a window's bounding rect: {x, y, width, height}."""
    from tools.window_ops import get_window_rect as legacy

    return ok(await legacy(title=title))


@tool("window_resize", category="ui", schema=WindowResizeInput, needs=("desktop",), timeout_s=10.0)
async def window_resize(title: str, width: int, height: int) -> dict:
    """Resize a window to `width` x `height`."""
    from tools.window_ops import resize_window as legacy

    return ok({"result": await legacy(title=title, width=width, height=height)})


@tool("window_move", category="ui", schema=WindowMoveInput, needs=("desktop",), timeout_s=10.0)
async def window_move(title: str, x: int, y: int) -> dict:
    """Move a window so its top-left corner is at (x, y)."""
    from tools.window_ops import move_window as legacy

    return ok({"result": await legacy(title=title, x=x, y=y)})
