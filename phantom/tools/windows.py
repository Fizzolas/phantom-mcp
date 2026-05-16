"""
phantom.tools.windows — list/focus/move/size desktop windows.

Wraps tools/window_ops.py. needs=("desktop",) so headless boxes hide
these. Each tool returns dict with predictable shape; failures include
available_titles to help the model retry.

Phase 3:
  * window_focus now verifies the focus change by calling window_active()
    after the legacy focus call and checking the active window title matches.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from phantom.contracts import ok, fail
from phantom.tools._base import tool


class NoArgsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WindowTitleInput(BaseModel):
    title: str = Field(..., min_length=1, description="Substring or exact window title.")
    strict: bool = Field(False, description="Require exact title match.")

    model_config = ConfigDict(extra="forbid")


class WindowMoveInput(BaseModel):
    title: str = Field(..., min_length=1)
    x: int = Field(..., ge=0)
    y: int = Field(..., ge=0)

    model_config = ConfigDict(extra="forbid")


class WindowResizeInput(BaseModel):
    title: str = Field(..., min_length=1)
    width: int = Field(..., gt=0)
    height: int = Field(..., gt=0)

    model_config = ConfigDict(extra="forbid")


class WindowOnlyTitleInput(BaseModel):
    title: str = Field(..., min_length=1)

    model_config = ConfigDict(extra="forbid")


@tool("window_list", category="windows", schema=NoArgsInput, needs=("desktop",), timeout_s=10.0)
async def window_list() -> dict:
    """
    List all visible windows with title, position, and size.
    """
    from tools.window_ops import list_windows as legacy
    return ok({"windows": await legacy()})


@tool("window_focus", category="windows", schema=WindowTitleInput, needs=("desktop",), timeout_s=10.0)
async def window_focus(title: str, strict: bool = False) -> dict:
    """
    Bring a window to the foreground and verify focus succeeded.

    With strict=False, matches by case-insensitive substring; on failure
    returns available_titles so the model can retry with a better title.

    Phase 3: after calling the legacy focus function, immediately calls
    window_active() to confirm the requested window is now focused.
    Returns verified: true/false.

    If verified is false, the legacy focus call may have succeeded but
    another window stole focus immediately afterward, or the OS blocked
    the focus change (common on Windows 10/11 when the calling process
    is in the background).
    """
    from tools.window_ops import focus_window as legacy, get_active_window
    result = await legacy(title, strict=strict)
    if isinstance(result, dict) and not result.get("ok", False):
        return fail(
            result.get("error") or f"No window matched {title!r}.",
            hint="Call window_list, then retry with a string from available_titles.",
            category="client_error",
            available_titles=result.get("available_titles", []),
        )

    # Verify the focus change by reading back the active window.
    active = get_active_window()
    focused_title = result.get("focused", "")
    actual_title = active.get("title", "")

    if actual_title and focused_title and focused_title.lower() in actual_title.lower():
        result["verified"] = True
    else:
        result["verified"] = False
        result["warning"] = f"Focus call succeeded but active window is '{actual_title}', not '{focused_title}'."

    return ok(result)


@tool("window_active", category="windows", schema=NoArgsInput, needs=("desktop",), timeout_s=5.0)
def window_active() -> dict:
    """
    Return the currently focused window's title, position, and size.
    """
    from tools.window_ops import get_active_window as legacy
    return ok(legacy())


@tool("window_minimize", category="windows", schema=WindowOnlyTitleInput, needs=("desktop",), timeout_s=10.0)
async def window_minimize(title: str) -> dict:
    """Minimize a window matched by title."""
    from tools.window_ops import minimize_window as legacy
    return ok({"message": await legacy(title)})


@tool("window_maximize", category="windows", schema=WindowOnlyTitleInput, needs=("desktop",), timeout_s=10.0)
async def window_maximize(title: str) -> dict:
    """Maximize a window matched by title."""
    from tools.window_ops import maximize_window as legacy
    return ok({"message": await legacy(title)})


@tool("window_restore", category="windows", schema=WindowOnlyTitleInput, needs=("desktop",), timeout_s=10.0)
async def window_restore(title: str) -> dict:
    """Restore a minimized/maximized window matched by title."""
    from tools.window_ops import restore_window as legacy
    return ok({"message": await legacy(title)})


@tool("window_get_rect", category="windows", schema=WindowOnlyTitleInput, needs=("desktop",), timeout_s=5.0)
async def window_get_rect(title: str) -> dict:
    """Return the position and size of a window."""
    from tools.window_ops import get_window_rect as legacy
    result = await legacy(title)
    if isinstance(result, dict) and "error" in result:
        return fail(result["error"], category="client_error")
    return ok(result)


@tool("window_resize", category="windows", schema=WindowResizeInput, needs=("desktop",), timeout_s=10.0)
async def window_resize(title: str, width: int, height: int) -> dict:
    """Resize a window matched by title."""
    from tools.window_ops import resize_window as legacy
    return ok({"message": await legacy(title, width, height)})


@tool("window_move", category="windows", schema=WindowMoveInput, needs=("desktop",), timeout_s=10.0)
async def window_move(title: str, x: int, y: int) -> dict:
    """Move a window matched by title to (x, y)."""
    from tools.window_ops import move_window as legacy
    return ok({"message": await legacy(title, x, y)})
