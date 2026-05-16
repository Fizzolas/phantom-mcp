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

Pass 6:
  * desktop_type: interval default 0.02->0.05, timeout 30->45, better docs
  * desktop_click: settle_ms param + confirm_screenshot pass-through, timeout 8->12
  * desktop_wait: new tool — explicit pause between actions
  * desktop_screen_info: better doc on call-first usage

Pass 7 — OCR + Screen Monitoring:
  * desktop_ocr: extract all text from a screen region via tesseract.
  * desktop_find_text: OCR + substring search; returns click coords if found.
  * desktop_wait_for_text: polls OCR until a string appears or timeout.
  * desktop_watch: watches a region for any visual change; no OCR required.
    Use after clicking buttons, typing text, launching apps, or pressing
    Enter — wait for the screen to actually react before the next action.

Pass 8 — Interaction Verification (Phase 3):
  * desktop_type: added verify=False param; when True, OCR reads back the
    target region after typing and returns match_ratio + mismatches list.
  * desktop_click: added verify_change=False param; when True, watches the
    click region for visual change and returns change_detected boolean.
  * desktop_key_press: added verify=False param for Enter/Tab/Escape; watches
    a center region for any screen change after the keypress.
  * All three now include 'verified: true/false' in result so the agent can
    detect when an action had no observable effect.
"""
from __future__ import annotations

import asyncio
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from phantom.contracts import ok, fail
from phantom.tools._base import tool


MouseButton = Literal["left", "right", "middle"]


# ----------------------------------------------------------------------
# Verification Helpers (Phase 3)
# ----------------------------------------------------------------------
async def _verify_typed_text(
    expected: str,
    region: str = "full",
    lang: str = "eng",
) -> dict:
    """
    OCR the region and compare to expected text.
    Returns {verified: bool, match_ratio: float, mismatches: list[str]}.
    """
    try:
        from tools.pc_vision import ocr_region
    except ImportError:
        return {
            "verified": False,
            "warning": "OCR verification requires tesseract; install it or pass verify=False.",
        }

    await asyncio.sleep(0.15)  # let the UI finish rendering the typed text
    try:
        ocr_result = await ocr_region(region=region, lang=lang)
        actual = ocr_result.get("text", "").strip()
    except Exception as e:
        return {
            "verified": False,
            "error": f"OCR failed: {e}",
        }

    if not actual:
        return {
            "verified": False,
            "match_ratio": 0.0,
            "mismatches": ["OCR returned empty string; expected text not visible."],
        }

    # Simple character-level comparison.
    expected_clean = expected.strip()
    matches = sum(1 for a, b in zip(actual, expected_clean) if a == b)
    total = max(len(actual), len(expected_clean))
    ratio = matches / total if total > 0 else 0.0

    mismatches = []
    if ratio < 0.95:
        mismatches.append(f"Expected: '{expected_clean[:60]}...'")
        mismatches.append(f"Actual:   '{actual[:60]}...'")
        if len(actual) < len(expected_clean):
            mismatches.append(f"Character drop: got {len(actual)} chars, expected {len(expected_clean)}.")

    return {
        "verified": ratio >= 0.95,
        "match_ratio": round(ratio, 3),
        "mismatches": mismatches,
    }


async def _quick_change_check(region: str = "800,400,400,300", threshold: float = 0.01) -> dict:
    """
    Watch a region for 400ms to detect any visual change.
    Returns {change_detected: bool, changed_fraction: float}.
    Used by desktop_key_press and desktop_click verification.
    """
    try:
        from tools.pc_vision import watch_region_for_change
    except ImportError:
        return {"change_detected": False, "warning": "visual verification unavailable"}

    try:
        watch_result = await watch_region_for_change(
            region=region,
            change_threshold=threshold,
            timeout_s=0.4,
            poll_s=0.1,
        )
        return {
            "change_detected": watch_result.get("changed", False),
            "changed_fraction": watch_result.get("changed_fraction", 0.0),
        }
    except Exception:
        return {"change_detected": False}


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

    PREFER desktop_ocr for reading text from the screen — it is faster,
    uses zero context tokens, and gives you a reliable string you can search.
    Only use this tool when you need to see layout, images, or visual state
    that OCR cannot describe.
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
# OCR
# ----------------------------------------------------------------------
class OcrInput(BaseModel):
    region: str = Field(
        "full",
        description="Screen region to read: 'full' or 'x,y,w,h' (e.g. '100,200,400,100').",
    )
    lang: str = Field(
        "eng",
        description="Tesseract language code. 'eng' for English. Use 'eng+fra' for multilingual.",
    )

    model_config = ConfigDict(extra="forbid")


@tool(
    "desktop_ocr",
    category="desktop",
    schema=OcrInput,
    needs=("desktop",),
    timeout_s=20.0,
)
async def desktop_ocr(region: str = "full", lang: str = "eng") -> dict:
    """
    Extract all text from a screen region using OCR (tesseract).

    Use this to:
      - Verify that text you typed actually appeared in the UI.
      - Read error messages, dialog content, or status text.
      - Confirm an app loaded the right content after navigation.
      - Read small text without burning context tokens on a screenshot.

    Returns the full extracted text as a string. Use desktop_find_text
    if you need to know WHERE a specific word appears so you can click it.

    Requires Tesseract-OCR to be installed on the system.
    Returns ok=false with a clear error + install hint if it is missing.
    """
    from tools.pc_vision import ocr_region
    return ok(await ocr_region(region=region, lang=lang))


class FindTextInput(BaseModel):
    needle: str = Field(..., description="The text or substring to search for on screen.")
    region: str = Field(
        "full",
        description="Screen region to search: 'full' or 'x,y,w,h'.",
    )
    case_sensitive: bool = Field(False, description="If false (default), search is case-insensitive.")
    lang: str = Field("eng", description="Tesseract language code.")

    model_config = ConfigDict(extra="forbid")


@tool(
    "desktop_find_text",
    category="desktop",
    schema=FindTextInput,
    needs=("desktop",),
    timeout_s=20.0,
)
async def desktop_find_text(
    needle: str,
    region: str = "full",
    case_sensitive: bool = False,
    lang: str = "eng",
) -> dict:
    """
    Search for text on screen via OCR. Returns found=True/False.

    If found, also returns:
      - click_x, click_y: center of the word's bounding box in screen
        coordinates. Pass these directly to desktop_click to click the text.
      - x, y, width, height: bounding box for visual reference.

    Use this when you need to:
      - Click a button or link whose position you don't know in advance.
      - Confirm a specific word or value is visible after an action.
      - Find the location of an error message to dismiss it.

    Prefer this over taking a screenshot and guessing coordinates.
    """
    from tools.pc_vision import find_text_on_screen
    result = await find_text_on_screen(
        needle, region=region, case_sensitive=case_sensitive, lang=lang
    )
    return ok(result)


class WaitForTextInput(BaseModel):
    needle: str = Field(..., description="Text to wait for.")
    region: str = Field("full", description="Screen region: 'full' or 'x,y,w,h'.")
    timeout_s: float = Field(
        15.0, ge=1.0, le=120.0,
        description="Max seconds to wait before giving up.",
    )
    poll_s: float = Field(
        1.0, ge=0.2, le=10.0,
        description="Seconds between OCR checks. Default 1s is a good balance.",
    )
    case_sensitive: bool = Field(False)
    lang: str = Field("eng")

    model_config = ConfigDict(extra="forbid")


@tool(
    "desktop_wait_for_text",
    category="desktop",
    schema=WaitForTextInput,
    needs=("desktop",),
    timeout_s=125.0,
)
async def desktop_wait_for_text(
    needle: str,
    region: str = "full",
    timeout_s: float = 15.0,
    poll_s: float = 1.0,
    case_sensitive: bool = False,
    lang: str = "eng",
) -> dict:
    """
    Wait until specific text appears on screen (via OCR), or timeout.

    Use this when:
      - You submitted a form or pressed Enter and need to wait for a
        confirmation message (e.g. 'Saved', 'Done', 'Success').
      - You launched an app and need to wait for it to finish loading
        (e.g. wait for 'File' menu to appear).
      - You are waiting for a long-running process to print a result.

    Returns {found: true, click_x, click_y} when the text appears so you
    can immediately act on it. Returns {found: false, timed_out: true} if
    the text never appeared within timeout_s.

    Prefer this over desktop_wait + desktop_screenshot + manual reading.
    It is more reliable and does not burn context tokens on images.
    """
    from tools.pc_vision import wait_for_text
    result = await wait_for_text(
        needle,
        region=region,
        timeout_s=timeout_s,
        poll_s=poll_s,
        case_sensitive=case_sensitive,
        lang=lang,
    )
    return ok(result)


class WatchInput(BaseModel):
    region: str = Field(
        "full",
        description="Region to monitor: 'full' or 'x,y,w,h'. Narrow regions are faster and more sensitive.",
    )
    change_threshold: float = Field(
        0.02,
        ge=0.001,
        le=1.0,
        description=(
            "Fraction of pixels that must change to count as 'changed'. "
            "0.02 = 2% (default, catches dialogs/spinners). "
            "0.005 for subtle changes like a single word appearing. "
            "0.10 for major transitions like a full page reload."
        ),
    )
    timeout_s: float = Field(15.0, ge=1.0, le=120.0, description="Max seconds to wait.")
    poll_s: float = Field(0.5, ge=0.1, le=5.0, description="Seconds between frames.")

    model_config = ConfigDict(extra="forbid")


@tool(
    "desktop_watch",
    category="desktop",
    schema=WatchInput,
    needs=("desktop",),
    timeout_s=125.0,
)
async def desktop_watch(
    region: str = "full",
    change_threshold: float = 0.02,
    timeout_s: float = 15.0,
    poll_s: float = 0.5,
) -> dict:
    """
    Watch a screen region and return when it changes visually.

    This is the most lightweight way to confirm an action had an effect.
    No OCR, no context tokens — just pixel comparison.

    Use this AFTER:
      - Pressing Enter or clicking a submit button — watch for the
        response to appear before reading or acting further.
      - Typing in a search box — watch for results to load.
      - Launching an app — watch for the window to appear.
      - Clicking a menu — watch for it to open.

    Returns:
      {changed: true, changed_fraction: 0.07, waited_s: 1.5}
      {changed: false, timed_out: true, timeout_s: 15.0}

    Pair with desktop_ocr or desktop_screenshot after this returns to
    read the new screen state.

    Tip: Use a narrow region (e.g. just the status bar or output area) for
    faster, more sensitive detection. Full-screen diff on 1080p has more
    noise (cursor movement, clock ticking) than a focused region.
    """
    from tools.pc_vision import watch_region_for_change
    result = await watch_region_for_change(
        region=region,
        change_threshold=change_threshold,
        timeout_s=timeout_s,
        poll_s=poll_s,
    )
    return ok(result)


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
    verify_change: bool = Field(
        False,
        description=(
            "If true, watches a 200px square region around the click point for visual change. "
            "Returns change_detected boolean. Use this to confirm buttons/links actually responded."
        ),
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
    verify_change: bool = False,
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

    To click text you found with desktop_find_text, use the returned
    click_x and click_y values directly as x and y here.

    Phase 3: verify_change=True watches a 200px square region centered on
    the click point for 400ms and returns change_detected boolean. Use this
    to confirm a button press or menu click actually had a visual effect.
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

    if verify_change:
        region = f"{max(0, x-100)},{max(0, y-100)},200,200"
        change_check = await _quick_change_check(region=region, threshold=0.01)
        result["verified"] = change_check["change_detected"]
        result["changed_fraction"] = change_check.get("changed_fraction", 0.0)
    else:
        result["verified"] = False

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

    PREFER desktop_watch or desktop_wait_for_text over this tool when possible.
    Those tools return as soon as the screen reacts rather than burning a fixed
    delay — they are faster and more reliable.

    Use desktop_wait only when you need an unconditional pause (e.g. giving
    a laggy input field time to receive focus before typing).
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
    verify: bool = Field(
        False,
        description=(
            "If true, OCR reads back the target region after typing to confirm text appeared. "
            "Returns verified, match_ratio, and mismatches list. Requires tesseract."
        ),
    )
    verify_region: str = Field(
        "full",
        description="Screen region to OCR for verification. Default 'full' or 'x,y,w,h'.",
    )
    verify_lang: str = Field(
        "eng",
        description="Tesseract language code for verification OCR.",
    )

    model_config = ConfigDict(extra="forbid")


@tool(
    "desktop_type",
    category="desktop",
    schema=KeyboardTypeInput,
    needs=("desktop",),
    timeout_s=45.0,
)
async def desktop_type(
    text: str,
    interval: float = 0.05,
    verify: bool = False,
    verify_region: str = "full",
    verify_lang: str = "eng",
) -> dict:
    """
    Type text into the currently-focused window.

    PREREQUISITES — do these BEFORE calling desktop_type:
      1. desktop_click on the target input field.
      2. Wait for focus: either use settle_ms in desktop_click (400+ for
         slow apps) OR call desktop_wait (500-1000ms for dialogs/browsers).
      3. Optionally desktop_screenshot to confirm the cursor is in the field.

    VERIFY AFTER TYPING (Phase 3):
      Pass verify=True to automatically OCR the target region after typing
      and confirm the text appeared. Returns:
        - verified: true if match_ratio >= 95%
        - match_ratio: 0.0-1.0 character-level match score
        - mismatches: list of error messages if text was dropped/garbled

      If OCR returns empty or the match is poor, retry with a higher interval
      or use desktop_hotkey('ctrl+a') to clear the field first.

    Behaviour:
      - Short text with only letters/digits/basic punctuation: typed directly
        key-by-key at `interval` seconds per character.
      - Text containing newlines, tabs, @, #, {, emoji, or other special
        characters: sent via clipboard paste (Ctrl+V) automatically.
      - The result includes 'method' ('direct_write' or 'clipboard') so you
        can diagnose drops.
    """
    from tools.mouse_kb import keyboard_type as legacy
    msg = await legacy(text, interval=interval)
    if isinstance(msg, dict):
        result = {**msg, "chars": len(text)}
    else:
        result = {"message": msg, "chars": len(text)}

    if verify:
        verify_result = await _verify_typed_text(text, region=verify_region, lang=verify_lang)
        result.update(verify_result)
    else:
        result["verified"] = False

    return ok(result)


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
    verify: bool = Field(
        False,
        description=(
            "If true and key is 'enter', 'tab', or 'escape', watches a center screen region "
            "for visual change after the keypress. Returns change_detected boolean."
        ),
    )

    model_config = ConfigDict(extra="forbid")


@tool(
    "desktop_key_press",
    category="desktop",
    schema=KeyboardPressInput,
    needs=("desktop",),
    timeout_s=5.0,
)
async def desktop_key_press(key: str, presses: int = 1, verify: bool = False) -> dict:
    """
    Press a single key one or more times.

    Phase 3: verify=True watches a 400x300 center region for visual change
    after the keypress. Use this for Enter, Tab, Escape — keys that trigger
    UI responses. Returns change_detected boolean.

    If change_detected is false after pressing Enter, the UI may not have
    accepted the input — check focus or retry.
    """
    from tools.mouse_kb import keyboard_press as legacy
    msg = await legacy(key, presses=presses)
    result = {"message": msg, "key": key, "presses": presses}

    if verify and key.lower() in ("enter", "tab", "escape"):
        change_check = await _quick_change_check()
        result["verified"] = change_check["change_detected"]
        result["change_detected"] = change_check["change_detected"]
    else:
        result["verified"] = False

    return ok(result)
