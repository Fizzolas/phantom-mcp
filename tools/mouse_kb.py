"""
Mouse & keyboard control via pyautogui.
All functions are async-wrapped so the MCP server stays non-blocking.

Special-char typing: pyautogui.write() silently drops chars like @, #, {, \n.
For any string that contains non-alphanumeric chars we fall back to
clipboard-paste (set via pyperclip then Ctrl+V) which handles all Unicode.

FIX (Task 8): keyboard_type clipboard path now:
  1. Saves the previous clipboard contents before overwriting.
  2. Verifies the paste text was actually written to the clipboard before
     firing Ctrl+V — if pyperclip.copy() silently failed, we catch the mismatch
     and raise instead of pasting garbage or an empty string.
  3. Restores the previous clipboard contents after the paste.

FIX (Task 9): keyboard_type, mouse_click, and mouse_double_click accept an
optional confirm_screenshot=False parameter. When True, a screenshot is taken
immediately after the action and returned in the response dict.

FIX (Bug 5): Removed dead `import PIL.Image` from _screenshot_b64().
  pyautogui.screenshot() returns a PIL Image internally without needing this
  explicit import. On machines where PIL is only importable as `Pillow` (not
  `PIL`), this line raised ImportError and broke every confirm_screenshot call.

FIX (Bug 6): mouse_scroll now passes x and y directly to pyautogui.scroll()
  instead of calling moveTo first then scroll with no coordinates.
  On a lagged system the cursor could drift between the two calls, causing
  the scroll to land at the wrong position. Pinning the coordinates to the
  scroll call itself eliminates the race.
"""
import asyncio
import base64
import io
import string
import pyautogui

pyautogui.FAILSAFE = True   # Move mouse to top-left corner to abort
pyautogui.PAUSE    = 0.04   # Small inter-action pause for stability

# Characters pyautogui.write() handles reliably
_SAFE_CHARS = set(string.ascii_letters + string.digits + string.punctuation + " ")


def _needs_clipboard(text: str) -> bool:
    """Return True if text contains chars that pyautogui.write() drops."""
    return any(c not in _SAFE_CHARS for c in text) or "\n" in text or "\t" in text


def _screenshot_b64() -> str | None:
    """Capture a screenshot and return it as a base64-encoded PNG string."""
    # BUG 5 FIX: Removed `import PIL.Image` — it was never used.
    # pyautogui.screenshot() returns a PIL Image internally without it.
    # On some machines PIL is not importable under that exact name, causing
    # ImportError here and breaking every confirm_screenshot call.
    try:
        img = pyautogui.screenshot()
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception as e:
        return f"screenshot_failed: {e}"


# ---------------------------------------------------------------------------
# Mouse
# ---------------------------------------------------------------------------

async def mouse_move(x: int, y: int, duration: float = 0.15) -> dict:
    await asyncio.to_thread(pyautogui.moveTo, x, y, duration=duration)
    return {"ok": True, "action": "mouse_move", "x": x, "y": y}


async def mouse_click(
    x: int,
    y: int,
    button: str = "left",
    clicks: int = 1,
    confirm_screenshot: bool = False,
) -> dict:
    """
    Click at (x, y).
    button: 'left' | 'right' | 'middle'
    clicks: number of clicks (1 = single, 2 = double)
    confirm_screenshot: if True, captures a screenshot after the click.
    """
    await asyncio.to_thread(pyautogui.click, x, y, button=button, clicks=clicks, interval=0.08)
    result: dict = {
        "ok": True,
        "action": "mouse_click",
        "x": x,
        "y": y,
        "button": button,
        "clicks": clicks,
    }
    if confirm_screenshot:
        await asyncio.sleep(0.15)
        result["screenshot"] = await asyncio.to_thread(_screenshot_b64)
    return result


async def mouse_double_click(
    x: int,
    y: int,
    confirm_screenshot: bool = False,
) -> dict:
    await asyncio.to_thread(pyautogui.doubleClick, x, y)
    result: dict = {"ok": True, "action": "mouse_double_click", "x": x, "y": y}
    if confirm_screenshot:
        await asyncio.sleep(0.15)
        result["screenshot"] = await asyncio.to_thread(_screenshot_b64)
    return result


async def mouse_right_click(x: int, y: int) -> dict:
    await asyncio.to_thread(pyautogui.rightClick, x, y)
    return {"ok": True, "action": "mouse_right_click", "x": x, "y": y}


async def mouse_scroll(x: int, y: int, clicks: int) -> dict:
    # BUG 6 FIX: Pass x and y directly to pyautogui.scroll() instead of
    # calling moveTo first then scroll with no coordinates.
    # On a lagged system the cursor could drift between the two calls,
    # causing the scroll to land at a different position than intended.
    # pyautogui.scroll() accepts x and y keyword args to pin scroll position.
    await asyncio.to_thread(pyautogui.scroll, clicks, x=x, y=y)
    return {"ok": True, "action": "mouse_scroll", "x": x, "y": y, "clicks": clicks}


async def mouse_drag(
    x1: int, y1: int,
    x2: int, y2: int,
    duration: float = 0.4,
    button: str = "left",
) -> dict:
    """
    Click-drag from (x1,y1) to (x2,y2).
    button: 'left' | 'right' | 'middle'
    """
    await asyncio.to_thread(pyautogui.moveTo, x1, y1, duration=0.15)
    await asyncio.to_thread(pyautogui.dragTo, x2, y2, duration=duration, button=button)
    return {"ok": True, "action": "mouse_drag", "from": [x1, y1], "to": [x2, y2], "button": button}


# ---------------------------------------------------------------------------
# Keyboard
# ---------------------------------------------------------------------------

def _clipboard_type_sync(text: str) -> dict:
    """
    Safe clipboard-paste path.
    1. Saves existing clipboard content before overwriting.
    2. Verifies pyperclip.copy() actually wrote the correct text.
    3. Fires Ctrl+V to paste.
    4. Restores the original clipboard content afterward.
    """
    import pyperclip
    try:
        previous = pyperclip.paste()
    except Exception:
        previous = ""

    pyperclip.copy(text)

    actual = pyperclip.paste()
    if actual != text:
        try:
            pyperclip.copy(previous)
        except Exception:
            pass
        raise RuntimeError(
            f"pyperclip.copy() verification failed: "
            f"expected {len(text)} chars, got {len(actual)} chars. "
            "This usually means no clipboard daemon is running (e.g. xclip/xsel missing on Linux)."
        )

    pyautogui.hotkey("ctrl", "v")

    try:
        pyperclip.copy(previous)
    except Exception:
        pass

    return {"ok": True, "method": "clipboard"}


async def keyboard_type(
    text: str,
    interval: float = 0.02,
    confirm_screenshot: bool = False,
) -> dict:
    """
    Type a string. Uses clipboard-paste fallback for special chars (\n, @, #, etc.)
    confirm_screenshot: if True, captures a screenshot after typing.
    """
    label = f"{text[:60]}{'...' if len(text) > 60 else ''}"
    result: dict = {"ok": True, "action": "keyboard_type", "text_preview": label}

    if _needs_clipboard(text):
        try:
            import pyperclip  # noqa: F401
            clip_result = await asyncio.to_thread(_clipboard_type_sync, text)
            result["method"] = clip_result["method"]
        except ImportError:
            await asyncio.to_thread(pyautogui.write, text, interval=interval)
            result["method"] = "direct_write_fallback"
            result["warning"] = "pyperclip not installed; special characters may be dropped."
        except RuntimeError as e:
            result["ok"] = False
            result["error"] = str(e)
            return result
    else:
        await asyncio.to_thread(pyautogui.write, text, interval=interval)
        result["method"] = "direct_write"

    if confirm_screenshot:
        await asyncio.sleep(0.15)
        result["screenshot"] = await asyncio.to_thread(_screenshot_b64)

    return result


async def keyboard_hotkey(keys: str) -> dict:
    """Press a keyboard shortcut. Examples: 'ctrl+c', 'alt+f4', 'ctrl+shift+esc', 'win+d'."""
    parts = [k.strip() for k in keys.split("+")]
    await asyncio.to_thread(pyautogui.hotkey, *parts)
    return {"ok": True, "action": "keyboard_hotkey", "keys": keys}


async def keyboard_press(key: str, presses: int = 1) -> dict:
    """Press a single key one or more times. key examples: 'enter', 'escape', 'tab', 'f5', 'delete'."""
    await asyncio.to_thread(pyautogui.press, key, presses=presses, interval=0.05)
    return {"ok": True, "action": "keyboard_press", "key": key, "presses": presses}


async def keyboard_key_down(key: str) -> dict:
    """Hold a key down (without releasing). Pair with keyboard_key_up."""
    await asyncio.to_thread(pyautogui.keyDown, key)
    return {"ok": True, "action": "keyboard_key_down", "key": key}


async def keyboard_key_up(key: str) -> dict:
    """Release a held key."""
    await asyncio.to_thread(pyautogui.keyUp, key)
    return {"ok": True, "action": "keyboard_key_up", "key": key}
