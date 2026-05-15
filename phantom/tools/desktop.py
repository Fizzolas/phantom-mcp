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

Pass 6 changes:
  * desktop_type
    - Default interval raised 0.02 → 0.05 (Win10 apps drop chars at 20ms).
    - timeout_s raised 30 → 45 (long pastes hit 30s on a loaded GPU box).
    - Description now explicitly tells the model to click the target and
      wait for focus BEFORE calling this tool, and to use desktop_wait
      after clicking if the target is a slow app.
    - Returns 'method' in the result so the model knows whether direct
      write or clipboard paste was used.
  * desktop_click
    - Added optional settle_ms (default 150ms) — waits after the click so
      the window has time to receive focus before the model types.
    - Added confirm_screenshot pass-through so the model can do
      click-and-verify in a single round trip.
    - timeout_s raised 8 → 12 to cover the settle window.
  * desktop_wait  (NEW)
    - Explicit sleep tool. Lets the model pause between actions when it
      knows a transition is slow (app launch, dialog open, page load).
    - Range: 100ms – 30s.  Model should describe WHY it is waiting so
      traces are readable.
  * desktop_screen_info
    - Description now tells the model to call this FIRST in any session
      and to use the returned bounds to validate coordinates before clicking.
"""
from __future__ import annotations

import asyncio
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

    Call this FIRST at the start of any desktop session. The returned
    'width' and 'height' are the coordinate bounds you must stay within
    for all mouse_* tools — coordinates outside these bounds will be
    rejected by pyautogui's failsafe or land in the wrong place.
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
    settle_ms: int = Field(
        150,
        ge=0,
        le=5000,
        description=(
            "Milliseconds to wait after the click before returning. "
            "Increase to 400–800 for dialogs, menus, or any UI that is slow to "
            "receive focus. Default 150ms covers most normal windows."
        ),
    )
    confirm_screenshot: bool = Field(
        False,
        description="If true, returns a screenshot in the result so you can verify the click landed correctly.",
    )

    model_config = ConfigDict(extra="forbid")


@tool(
    "desktop_click",
    category="desktop",
    schema=MouseClickInput,
    needs=("desktop",),
    timeout_s=12.0,
)
async def desktop_click(
    x: int,
    y: int,
    button: str = "left",
    clicks: int = 1,
    settle_ms: int = 150,
    confirm_screenshot: bool = False,
) -> dict:
    """
    Click at screen coordinate (x, y) then wait for focus to settle.

    IMPORTANT — click THEN type, never simultaneously:
      1. Call desktop_click on the input field.
      2. If the target is a slow app (browser address bar, dialog, terminal),
         increase settle_ms to 400-800 or follow with desktop_wait.
      3. Only then call desktop_type.

    For double-click pass clicks=2. For right-click pass button="right".
    Set confirm_screenshot=True to verify the click landed before typing.
    """
    from tools.mouse_kb import mouse_click as legacy
    msg = await legacy(x, y, button=button, clicks=clicks, confirm_screenshot=confirm_screenshot)
    if settle_ms > 0:
        await asyncio.sleep(settle_ms / 1000.0)
    result = {"x": x, "y": y, "button": button, "clicks": clicks, "settle_ms": settle_ms}
    if isinstance(msg, dict):
        result.update(msg)
    else:
        result["message"] = msg
    return ok(result)


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
# Wait
# ----------------------------------------------------------------------
class WaitInput(BaseModel):
    ms: int = Field(
        500,
        ge=100,
        le=30000,
        description="How long to wait in milliseconds (100–30000). Use 500 for normal UI transitions, 1000–3000 for app launches or page loads.",
    )
    reason: str = Field(
        "",
        description="Why you are waiting (e.g. 'waiting for dialog to open', 'letting browser load'). Logged to traces.",
    )

    model_config = ConfigDict(extra="forbid")


@tool(
    "desktop_wait",
    category="desktop",
    schema=WaitInput,
    needs=("desktop",),
    timeout_s=35.0,
)
async def desktop_wait(ms: int = 500, reason: str = "") -> dict:
    """
    Pause for `ms` milliseconds before continuing.

    Use this when:
      - You opened an app or window and need it to finish loading.
      - You clicked a button that triggers a slow transition (dialog, page load).
      - You pressed a hotkey and need the UI to respond before reading the screen.
      - You are about to type and the focus target was slow to appear.

    Typical values:
      - 300–500ms  : normal button/menu/focus transition
      - 800–1500ms : dialog opening, browser tab loading
      - 2000–5000ms: app launch (Notepad, Explorer, IDE)
      - 5000–10000ms: heavy app launch (browser cold start, IDE indexing)

    Always follow with desktop_screenshot to confirm the UI is ready.
    """
    await asyncio.sleep(ms / 1000.0)
    return ok({"waited_ms": ms, "reason": reason or "(no reason given)"})


# ----------------------------------------------------------------------
# Keyboard
# ----------------------------------------------------------------------
class KeyboardTypeInput(BaseModel):
    text: str = Field(..., description="Text to type into the currently-focused window.")
    interval: float = Field(
        0.05,
        ge=0.0,
        le=1.0,
        description=(
            "Delay in seconds between each keypress for direct-write mode. "
            "Default 0.05s (50ms) is safe for Win10. Reduce to 0.02 only for "
            "fast terminals; increase to 0.10 if characters are still dropped."
        ),
    )

    model_config = ConfigDict(extra="forbid")


@tool(
    "desktop_type",
    category="desktop",
    schema=KeyboardTypeInput,
    needs=("desktop",),
    timeout_s=45.0,
)
async def desktop_type(text: str, interval: float = 0.05) -> dict:
    """
    Type text into the currently-focused window.

    PREREQUISITES — do these BEFORE calling desktop_type:
      1. desktop_click on the target input field.
      2. Wait for focus: either use settle_ms in desktop_click (400+ for
         slow apps) OR call desktop_wait (500-1000ms for dialogs/browsers).
      3. Optionally desktop_screenshot to confirm the cursor is in the field.

    Behaviour:
      - Short text with only letters/digits/basic punctuation: typed directly
        key-by-key at `interval` seconds per character.
      - Text containing newlines, tabs, @, #, {, emoji, or other special
        characters: sent via clipboard paste (Ctrl+V) automatically.
      - The result includes 'method' ('direct_write' or 'clipboard') so you
        can diagnose drops.

    If characters are still being dropped after following prerequisites:
      - Increase interval to 0.10.
      - Use desktop_hotkey('ctrl+a') first to clear the field.
      - Consider shell_run for programmatic input instead of GUI typing.
    """
    from tools.mouse_kb import keyboard_type as legacy
    msg = await legacy(text, interval=interval)
    if isinstance(msg, dict):
        return ok({**msg, "chars": len(text)})
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
