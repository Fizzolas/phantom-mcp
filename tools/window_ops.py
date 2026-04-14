"""
Window management using pygetwindow.
Added: get_window_rect, resize_window, move_window, restore_window.
Fixed: focus_window now restores minimized windows before activating.
"""
import asyncio

try:
    import pygetwindow as gw
    HAS_GW = True
except ImportError:
    HAS_GW = False


def _no_gw():
    return {"error": "pygetwindow not available. Run: pip install pygetwindow"}


def _find(title: str):
    """Return list of windows whose title contains `title` (case-insensitive)."""
    return [w for w in gw.getAllWindows() if title.lower() in w.title.lower()]


async def list_windows() -> list:
    """List all visible window titles with their position and size."""
    if not HAS_GW:
        return [_no_gw()]
    def _get():
        results = []
        for w in gw.getAllWindows():
            if not w.title.strip():
                continue
            try:
                results.append({
                    "title": w.title,
                    "left": w.left,
                    "top": w.top,
                    "width": w.width,
                    "height": w.height,
                    "minimized": w.isMinimized,
                    "active": w.isActive,
                })
            except Exception:
                results.append({"title": w.title})
        return results
    return await asyncio.to_thread(_get)


async def focus_window(title: str) -> str:
    """Bring a window to the foreground. Restores it first if minimized."""
    if not HAS_GW:
        return _no_gw()
    matches = _find(title)
    if not matches:
        return f"No window found matching: '{title}'"
    w = matches[0]
    try:
        if w.isMinimized:
            w.restore()
            await asyncio.sleep(0.15)
        w.activate()
        return f"Focused: '{w.title}'"
    except Exception as e:
        return f"Focus failed for '{w.title}': {e}"


def get_active_window() -> dict:
    """Return title, position, and size of the currently focused window."""
    if not HAS_GW:
        return _no_gw()
    w = gw.getActiveWindow()
    if not w:
        return {"title": None, "message": "No active window"}
    try:
        return {
            "title": w.title,
            "left": w.left,
            "top": w.top,
            "width": w.width,
            "height": w.height,
        }
    except Exception:
        return {"title": w.title}


async def minimize_window(title: str) -> str:
    if not HAS_GW:
        return _no_gw()
    matches = _find(title)
    if not matches:
        return f"No window: '{title}'"
    matches[0].minimize()
    return f"Minimized: '{matches[0].title}'"


async def maximize_window(title: str) -> str:
    if not HAS_GW:
        return _no_gw()
    matches = _find(title)
    if not matches:
        return f"No window: '{title}'"
    matches[0].maximize()
    return f"Maximized: '{matches[0].title}'"


async def restore_window(title: str) -> str:
    """Restore a minimized/maximized window to its normal size."""
    if not HAS_GW:
        return _no_gw()
    matches = _find(title)
    if not matches:
        return f"No window: '{title}'"
    matches[0].restore()
    return f"Restored: '{matches[0].title}'"


async def get_window_rect(title: str) -> dict:
    """Get the exact position and size of a window by title."""
    if not HAS_GW:
        return _no_gw()
    matches = _find(title)
    if not matches:
        return {"error": f"No window matching: '{title}'"}
    w = matches[0]
    try:
        return {
            "title": w.title,
            "left": w.left,
            "top": w.top,
            "width": w.width,
            "height": w.height,
            "right": w.left + w.width,
            "bottom": w.top + w.height,
            "center_x": w.left + w.width // 2,
            "center_y": w.top + w.height // 2,
        }
    except Exception as e:
        return {"error": str(e)}


async def resize_window(title: str, width: int, height: int) -> str:
    """Resize a window to the given width and height in pixels."""
    if not HAS_GW:
        return _no_gw()
    matches = _find(title)
    if not matches:
        return f"No window: '{title}'"
    try:
        matches[0].resizeTo(width, height)
        return f"Resized '{matches[0].title}' to {width}x{height}"
    except Exception as e:
        return f"Resize failed: {e}"


async def move_window(title: str, x: int, y: int) -> str:
    """Move a window's top-left corner to (x, y)."""
    if not HAS_GW:
        return _no_gw()
    matches = _find(title)
    if not matches:
        return f"No window: '{title}'"
    try:
        matches[0].moveTo(x, y)
        return f"Moved '{matches[0].title}' to ({x}, {y})"
    except Exception as e:
        return f"Move failed: {e}"
