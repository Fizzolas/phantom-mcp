"""
Window management using pygetwindow + win32gui fallback.

FIX (sweep-2): focus_window now uses win32gui.SetForegroundWindow with
AttachThreadInput so Windows 10/11 actually brings the window to front instead
of just flashing it in the taskbar. Without this, every click after focus_window
lands on the wrong window.

Added: get_window_rect, resize_window, move_window, restore_window.
"""
import asyncio
import ctypes

try:
    import pygetwindow as gw
    HAS_GW = True
except ImportError:
    HAS_GW = False

try:
    import win32gui
    import win32con
    import win32process
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False


def _no_gw():
    return {"error": "pygetwindow not available. Run: pip install pygetwindow"}


def _find(title: str):
    """Return list of windows whose title contains `title` (case-insensitive)."""
    return [w for w in gw.getAllWindows() if title.lower() in w.title.lower()]


def _force_foreground(hwnd: int) -> bool:
    """
    FIX: Force a window to the foreground on Windows 10/11.
    Standard SetForegroundWindow is silently ignored when called from a background
    process — the window just flashes in the taskbar. Attaching the calling thread's
    input queue to the target window's thread bypasses this restriction.
    """
    if not HAS_WIN32:
        return False
    try:
        # Get foreground window's thread
        fg_hwnd = win32gui.GetForegroundWindow()
        fg_tid, _ = win32process.GetWindowThreadProcessId(fg_hwnd)
        # Get target window's thread
        target_tid, _ = win32process.GetWindowThreadProcessId(hwnd)
        current_tid = ctypes.windll.kernel32.GetCurrentThreadId()

        # Attach input threads so we're allowed to call SetForegroundWindow
        attached_fg = False
        attached_self = False
        if fg_tid != current_tid:
            win32process.AttachThreadInput(current_tid, fg_tid, True)
            attached_fg = True
        if target_tid != current_tid and target_tid != fg_tid:
            win32process.AttachThreadInput(current_tid, target_tid, True)
            attached_self = True

        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        win32gui.BringWindowToTop(hwnd)

        # Detach thread inputs
        if attached_fg:
            win32process.AttachThreadInput(current_tid, fg_tid, False)
        if attached_self:
            win32process.AttachThreadInput(current_tid, target_tid, False)
        return True
    except Exception:
        return False


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
    """
    Bring a window to the foreground.
    FIX: Uses AttachThreadInput path via _force_foreground() so the window
    actually gets focus on Windows 10/11 (not just a taskbar flash).
    Falls back to pygetwindow.activate() if win32gui is unavailable.
    """
    if not HAS_GW:
        return str(_no_gw())
    matches = _find(title)
    if not matches:
        return f"No window found matching: '{title}'"
    w = matches[0]
    try:
        if w.isMinimized:
            w.restore()
            await asyncio.sleep(0.2)

        if HAS_WIN32:
            # Get the real HWND from pygetwindow and use the reliable force path
            hwnd = w._hWnd if hasattr(w, "_hWnd") else win32gui.FindWindow(None, w.title)
            if hwnd and _force_foreground(hwnd):
                await asyncio.sleep(0.1)
                return f"Focused (win32): '{w.title}'"

        # Fallback to pygetwindow activate
        w.activate()
        return f"Focused (gw): '{w.title}'"
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
        return str(_no_gw())
    matches = _find(title)
    if not matches:
        return f"No window: '{title}'"
    matches[0].minimize()
    return f"Minimized: '{matches[0].title}'"


async def maximize_window(title: str) -> str:
    if not HAS_GW:
        return str(_no_gw())
    matches = _find(title)
    if not matches:
        return f"No window: '{title}'"
    matches[0].maximize()
    return f"Maximized: '{matches[0].title}'"


async def restore_window(title: str) -> str:
    """Restore a minimized/maximized window to its normal size."""
    if not HAS_GW:
        return str(_no_gw())
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
        return str(_no_gw())
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
        return str(_no_gw())
    matches = _find(title)
    if not matches:
        return f"No window: '{title}'"
    try:
        matches[0].moveTo(x, y)
        return f"Moved '{matches[0].title}' to ({x}, {y})"
    except Exception as e:
        return f"Move failed: {e}"
