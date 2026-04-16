"""
phantom.tools.mouse_kb — mouse + keyboard control.

Legacy had 11 separate tools (mouse_click, mouse_double_click,
mouse_right_click, etc.). PR 3 collapses to 4 typed tools:

  mouse_move     just move the cursor
  mouse_click    x, y, button, clicks   (handles single/double/right/left)
  mouse_scroll   x, y, clicks           (positive up, negative down)
  mouse_drag     from_x, from_y, to_x, to_y

  keyboard_type  type a string
  keyboard_key   press a single key or hotkey, optionally multiple times

That's 6 tools where there used to be 11 — less context noise for the
model. All gated behind needs=("desktop",).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from phantom.contracts import ok
from phantom.tools._base import tool


Button = Literal["left", "right", "middle"]


class MouseMoveInput(BaseModel):
    x: int = Field(..., ge=0)
    y: int = Field(..., ge=0)
    duration_s: float = Field(0.15, ge=0.0, le=5.0)
    model_config = ConfigDict(extra="forbid")


class MouseClickInput(BaseModel):
    x: int = Field(..., ge=0)
    y: int = Field(..., ge=0)
    button: Button = Field("left")
    clicks: int = Field(1, ge=1, le=5)
    model_config = ConfigDict(extra="forbid")


class MouseScrollInput(BaseModel):
    x: int = Field(..., ge=0)
    y: int = Field(..., ge=0)
    clicks: int = Field(..., description="Positive scrolls up, negative down.")
    model_config = ConfigDict(extra="forbid")


class MouseDragInput(BaseModel):
    from_x: int = Field(..., ge=0)
    from_y: int = Field(..., ge=0)
    to_x: int = Field(..., ge=0)
    to_y: int = Field(..., ge=0)
    duration_s: float = Field(0.3, ge=0.0, le=5.0)
    button: Button = Field("left")
    model_config = ConfigDict(extra="forbid")


class KeyboardTypeInput(BaseModel):
    text: str = Field(..., description="Literal text to type.")
    interval_s: float = Field(0.02, ge=0.0, le=1.0, description="Per-character delay.")
    model_config = ConfigDict(extra="forbid")


class KeyboardKeyInput(BaseModel):
    key: str = Field(
        ...,
        min_length=1,
        description="A single key ('enter'), or '+' separated hotkey ('ctrl+c').",
    )
    presses: int = Field(1, ge=1, le=10)
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------


@tool("mouse_move", category="input", schema=MouseMoveInput, needs=("desktop",), timeout_s=10.0)
async def mouse_move(x: int, y: int, duration_s: float = 0.15) -> dict:
    """Smoothly move the cursor to screen coordinates (x, y)."""
    from tools.mouse_kb import mouse_move as legacy

    return ok({"result": await legacy(x=x, y=y, duration=duration_s)})


@tool("mouse_click", category="input", schema=MouseClickInput, needs=("desktop",), timeout_s=10.0)
async def mouse_click(x: int, y: int, button: str = "left", clicks: int = 1) -> dict:
    """
    Click at (x, y). Use `clicks=2` for double-click, `button='right'` for
    right-click. No more separate mouse_double_click / mouse_right_click tools.
    """
    from tools.mouse_kb import mouse_click as legacy

    return ok({"result": await legacy(x=x, y=y, button=button, clicks=clicks)})


@tool("mouse_scroll", category="input", schema=MouseScrollInput, needs=("desktop",), timeout_s=10.0)
async def mouse_scroll(x: int, y: int, clicks: int) -> dict:
    """Scroll at (x, y). Positive `clicks` scrolls up, negative down."""
    from tools.mouse_kb import mouse_scroll as legacy

    return ok({"result": await legacy(x=x, y=y, clicks=clicks)})


@tool("mouse_drag", category="input", schema=MouseDragInput, needs=("desktop",), timeout_s=15.0)
async def mouse_drag(
    from_x: int,
    from_y: int,
    to_x: int,
    to_y: int,
    duration_s: float = 0.3,
    button: str = "left",
) -> dict:
    """Drag the cursor from one point to another while holding `button`."""
    from tools.mouse_kb import mouse_drag as legacy

    return ok(
        {
            "result": await legacy(
                from_x=from_x, from_y=from_y, to_x=to_x, to_y=to_y,
                duration=duration_s, button=button,
            )
        }
    )


@tool("keyboard_type", category="input", schema=KeyboardTypeInput, needs=("desktop",), timeout_s=30.0)
async def keyboard_type(text: str, interval_s: float = 0.02) -> dict:
    """Type `text` at the current focus, character by character."""
    from tools.mouse_kb import keyboard_type as legacy

    return ok({"result": await legacy(text=text, interval=interval_s)})


@tool("keyboard_key", category="input", schema=KeyboardKeyInput, needs=("desktop",), timeout_s=10.0)
async def keyboard_key(key: str, presses: int = 1) -> dict:
    """
    Press a single key or hotkey combination. Pass 'enter', 'esc',
    'ctrl+c', 'ctrl+shift+t', etc. `presses` repeats the press.
    """
    from tools.mouse_kb import keyboard_hotkey as legacy_hotkey, keyboard_press as legacy_press

    if "+" in key:
        # hotkey
        results = []
        for _ in range(presses):
            results.append(await legacy_hotkey(keys=key))
        return ok({"results": results})
    return ok({"result": await legacy_press(key=key, presses=presses)})
