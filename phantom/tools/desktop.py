"""
phantom.tools.desktop — desktop input + vision tools.

These wrap the legacy tools/mouse_kb.py and tools/pc_vision.py functions
in the new ToolResult envelope and expose them under task-oriented
desktop_* names, so the LM Studio model never confuses them with browser
or general-purpose tools.

Design notes:
  * needs=("desktop",) — hidden on headless boxes.
  * Strict pydantic schemas — bad coordinates/buttons rejected pre-call.
  * Each tool returns a small, structured dict so the model can read
    "what happened" in one short blob (no giant logs).
  * Screenshot returns base64 JPEG, with size + chars in meta so the
    model can decide whether it can afford another one.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from phantom.contracts import ok, fail
from phantom.tools._base import tool


MouseButton = Literal["left", "right", "middle"]


# ----------------------------------------------------------------------
# Vision
# ----------------------------------------------------------------------
class ScreenshotInput(BaseModel):
    region: str = Field(
        "full",
        description="Either 'full' for primary monitor, or 'x,y,w,h' for a region.",
    )
    hires: bool = Field(
        False,
        description="If true, return full-res PNG (heavy — only use when small text matters).",
    )

    model_config = ConfigDict(extra="forbid")


@tool(
    "desktop_screenshot",
    category="desktop",
    schema=ScreenshotInput,
    needs=("desktop",),
    timeout_s=15.0,
)
async def desktop_screenshot(region: str = "full", hires: bool = False) -> dict:
    """
    Capture the screen and return a base64-encoded image.

    Use this AFTER any visual change you make so you can verify the
    result before the next action. Default returns a downscaled JPEG
    (~2.5k tokens). Set hires=True only when you need to read fine text.
    """
    if hires:
        from tools.pc_vision import take_screenshot_hires as legacy
        b64 = await legacy(region)
        return ok(
            {"image_base64": b64, "format": "png", "region": region},
            hint="Hi-res PNG; large token cost. Prefer the JPEG default next time.",
            chars=len(b64),
        )

    from tools.pc_vision import take_screenshot as legacy
    b64 = await legacy(region)
    return ok(
        {"image_base64": b64, "format": "jpeg", "region": region},
        chars=len(b64),
    )


class ScreenInfoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


@tool(
    "desktop_screen_info",
    category="desktop",
    schema=ScreenInfoInput,
    needs=("desktop",),
    timeout_s=3.0,
)
def desktop_screen_info() -> dict:
    """
    Return primary screen size and screenshot settings.

    Call once at the start of a session to know the coordinate bounds for
    mouse_* tools.
    """
    from tools.pc_vision import get_screen_info
    return ok(get_screen_info())


# ----------------------------------------------------------------------
# Mouse
# ----------------------------------------------------------------------
class MouseClickInput(BaseModel):
    x: int = Field(..., ge=0)
    y: int = Field(..., ge=0)
    button: MouseButton = Field("left")
    clicks: int = Field(1, ge=1, le=3)

    model_config = ConfigDict(extra="forbid")


@tool(
    "desktop_click",
    category="desktop",
    schema=MouseClickInput,
    needs=("desktop",),
    timeout_s=8.0,
)
async def desktop_click(x: int, y: int, button: str = "left", clicks: int = 1) -> dict:
    """
    Click at screen coordinate (x, y).

    For double-click pass clicks=2. For right-click pass button="right".
    Pair with desktop_screenshot afterwards to confirm the result.
    """
    from tools.mouse_kb import mouse_click as legacy
    msg = await legacy(x, y, button=button, clicks=clicks)
    return ok({"message": msg, "x": x, "y": y, "button": button, "clicks": clicks})


class MouseMoveInput(BaseModel):
    x: int = Field(..., ge=0)
    y: int = Field(..., ge=0)
    duration: float = Field(0.15, ge=0.0, le=2.0)

    model_config = ConfigDict(extra="forbid")


@tool(
    "desktop_move",
    category="desktop",
    schema=MouseMoveInput,
    needs=("desktop",),
    timeout_s=5.0,
)
async def desktop_move(x: int, y: int, duration: float = 0.15) -> dict:
    """
    Move the mouse cursor to (x, y) without clicking.
    """
    from tools.mouse_kb import mouse_move as legacy
    msg = await legacy(x, y, duration=duration)
    return ok({"message": msg, "x": x, "y": y})


class MouseScrollInput(BaseModel):
    x: int = Field(..., ge=0)
    y: int = Field(..., ge=0)
    clicks: int = Field(..., ge=-50, le=50, description="Positive scrolls up, negative scrolls down.")

    model_config = ConfigDict(extra="forbid")


@tool(
    "desktop_scroll",
    category="desktop",
    schema=MouseScrollInput,
    needs=("desktop",),
    timeout_s=5.0,
)
async def desktop_scroll(x: int, y: int, clicks: int) -> dict:
    """
    Scroll the wheel `clicks` notches at position (x, y).
    """
    from tools.mouse_kb import mouse_scroll as legacy
    msg = await legacy(x, y, clicks)
    return ok({"message": msg, "clicks": clicks})


class MouseDragInput(BaseModel):
    x1: int = Field(..., ge=0)
    y1: int = Field(..., ge=0)
    x2: int = Field(..., ge=0)
    y2: int = Field(..., ge=0)
    button: MouseButton = Field("left")
    duration: float = Field(0.4, ge=0.0, le=5.0)

    model_config = ConfigDict(extra="forbid")


@tool(
    "desktop_drag",
    category="desktop",
    schema=MouseDragInput,
    needs=("desktop",),
    timeout_s=10.0,
)
async def desktop_drag(
    x1: int, y1: int, x2: int, y2: int,
    button: str = "left", duration: float = 0.4,
) -> dict:
    """
    Click-and-drag from (x1,y1) to (x2,y2).
    """
    from tools.mouse_kb import mouse_drag as legacy
    msg = await legacy(x1, y1, x2, y2, duration=duration, button=button)
    return ok({"message": msg, "from": [x1, y1], "to": [x2, y2]})


# ----------------------------------------------------------------------
# Keyboard
# ----------------------------------------------------------------------
class KeyboardTypeInput(BaseModel):
    text: str = Field(..., description="Text to type. Newlines/special chars use clipboard fallback.")
    interval: float = Field(0.02, ge=0.0, le=1.0)

    model_config = ConfigDict(extra="forbid")


@tool(
    "desktop_type",
    category="desktop",
    schema=KeyboardTypeInput,
    needs=("desktop",),
    timeout_s=30.0,
)
async def desktop_type(text: str, interval: float = 0.02) -> dict:
    """
    Type text at the current focus. The currently-focused window will
    receive the keystrokes.

    For long text or text with special characters (newlines, emoji,
    punctuation that pyautogui drops) this falls back to clipboard paste.
    """
    from tools.mouse_kb import keyboard_type as legacy
    msg = await legacy(text, interval=interval)
    return ok({"message": msg, "chars": len(text)})


class KeyboardHotkeyInput(BaseModel):
    keys: str = Field(..., description="Plus-separated keys, e.g. 'ctrl+c', 'alt+tab', 'win+d'.")

    model_config = ConfigDict(extra="forbid")


@tool(
    "desktop_hotkey",
    category="desktop",
    schema=KeyboardHotkeyInput,
    needs=("desktop",),
    timeout_s=5.0,
)
async def desktop_hotkey(keys: str) -> dict:
    """
    Press a keyboard shortcut. Examples: 'ctrl+c', 'alt+f4', 'win+d'.
    """
    from tools.mouse_kb import keyboard_hotkey as legacy
    msg = await legacy(keys)
    return ok({"message": msg, "keys": keys})


class KeyboardPressInput(BaseModel):
    key: str = Field(..., description="Single key like 'enter', 'escape', 'tab', 'f5'.")
    presses: int = Field(1, ge=1, le=20)

    model_config = ConfigDict(extra="forbid")


@tool(
    "desktop_key_press",
    category="desktop",
    schema=KeyboardPressInput,
    needs=("desktop",),
    timeout_s=5.0,
)
async def desktop_key_press(key: str, presses: int = 1) -> dict:
    """
    Press a single key one or more times.
    """
    from tools.mouse_kb import keyboard_press as legacy
    msg = await legacy(key, presses=presses)
    return ok({"message": msg, "key": key, "presses": presses})
