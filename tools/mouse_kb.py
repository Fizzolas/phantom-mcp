"""
Mouse & keyboard control via pyautogui.
All functions are async-wrapped so the MCP server stays non-blocking.

Special-char typing: pyautogui.write() silently drops chars like @, #, {, \n.
For any string that contains non-alphanumeric chars we fall back to
clipboard-paste (set via pyperclip then Ctrl+V) which handles all Unicode.
"""
import asyncio
import string
import pyautogui

pyautogui.FAILSAFE = True   # Move mouse to top-left corner to abort
pyautogui.PAUSE    = 0.04   # Small inter-action pause for stability

# Characters pyautogui.write() handles reliably
_SAFE_CHARS = set(string.ascii_letters + string.digits + string.punctuation + " ")


def _needs_clipboard(text: str) -> bool:
    """Return True if text contains chars that pyautogui.write() drops."""
    return any(c not in _SAFE_CHARS for c in text) or "\n" in text or "\t" in text


# ---------------------------------------------------------------------------
# Mouse
# ---------------------------------------------------------------------------

async def mouse_move(x: int, y: int, duration: float = 0.15) -> str:
    await asyncio.to_thread(pyautogui.moveTo, x, y, duration=duration)
    return f"Mouse moved to ({x}, {y})"


async def mouse_click(x: int, y: int, button: str = "left", clicks: int = 1) -> str:
    """
    Click at (x, y).
    button: 'left' | 'right' | 'middle'
    clicks: number of clicks (1 = single, 2 = double)
    """
    await asyncio.to_thread(pyautogui.click, x, y, button=button, clicks=clicks, interval=0.08)
    return f"{'Double-' if clicks == 2 else ''}Clicked ({x}, {y}) [{button}]"


async def mouse_double_click(x: int, y: int) -> str:
    await asyncio.to_thread(pyautogui.doubleClick, x, y)
    return f"Double-clicked ({x}, {y})"


async def mouse_right_click(x: int, y: int) -> str:
    await asyncio.to_thread(pyautogui.rightClick, x, y)
    return f"Right-clicked ({x}, {y})"


async def mouse_scroll(x: int, y: int, clicks: int) -> str:
    await asyncio.to_thread(pyautogui.moveTo, x, y, duration=0.1)
    await asyncio.to_thread(pyautogui.scroll, clicks)
    return f"Scrolled {clicks} clicks at ({x}, {y})"


async def mouse_drag(
    x1: int, y1: int,
    x2: int, y2: int,
    duration: float = 0.4,
    button: str = "left",
) -> str:
    """
    Click-drag from (x1,y1) to (x2,y2).
    Useful for window repositioning, selecting text, Slider controls.
    button: 'left' | 'right' | 'middle'
    """
    await asyncio.to_thread(
        pyautogui.drag,
        x2 - x1, y2 - y1,   # relative offsets
        duration=duration,
        button=button,
        _pause=False,
    )
    # pyautogui.drag works from current pos, so move first
    await asyncio.to_thread(pyautogui.moveTo, x1, y1, duration=0.1)
    await asyncio.to_thread(pyautogui.dragTo, x2, y2, duration=duration, button=button)
    return f"Dragged [{button}] ({x1},{y1}) -> ({x2},{y2})"


# ---------------------------------------------------------------------------
# Keyboard
# ---------------------------------------------------------------------------

async def keyboard_type(text: str, interval: float = 0.02) -> str:
    """
    Type a string. Uses clipboard-paste fallback for special chars (\n, @, #, etc.)
    interval: seconds between keystrokes (only used in direct-write path)
    """
    if _needs_clipboard(text):
        # Clipboard path: handles all Unicode, newlines, special chars
        try:
            import pyperclip
            await asyncio.to_thread(pyperclip.copy, text)
            await asyncio.to_thread(pyautogui.hotkey, "ctrl", "v")
            return f"Typed via clipboard ({len(text)} chars): {text[:60]}{'...' if len(text)>60 else ''}"
        except ImportError:
            pass  # fall through to direct write
    await asyncio.to_thread(pyautogui.write, text, interval=interval)
    return f"Typed: {text[:60]}{'...' if len(text)>60 else ''}"


async def keyboard_hotkey(keys: str) -> str:
    """Press a keyboard shortcut. Examples: 'ctrl+c', 'alt+f4', 'ctrl+shift+esc', 'win+d'."""
    parts = [k.strip() for k in keys.split("+")]
    await asyncio.to_thread(pyautogui.hotkey, *parts)
    return f"Hotkey: {keys}"


async def keyboard_press(key: str, presses: int = 1) -> str:
    """Press a single key one or more times. key examples: 'enter', 'escape', 'tab', 'f5', 'delete'."""
    await asyncio.to_thread(pyautogui.press, key, presses=presses, interval=0.05)
    return f"Key '{key}' x{presses}"


async def keyboard_key_down(key: str) -> str:
    """Hold a key down (without releasing). Pair with keyboard_key_up."""
    await asyncio.to_thread(pyautogui.keyDown, key)
    return f"Key down: {key}"


async def keyboard_key_up(key: str) -> str:
    """Release a held key."""
    await asyncio.to_thread(pyautogui.keyUp, key)
    return f"Key up: {key}"
