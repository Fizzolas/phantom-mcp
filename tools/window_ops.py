"""
Window management using pygetwindow + win32gui fallback.

FIX (sweep-2): focus_window now uses win32gui.SetForegroundWindow with
AttachThreadInput so Windows 10/11 actually brings the window to front instead
of just flashing it in the taskbar. Without this, every click after focus_window
lands on the wrong window.

Added: get_window_rect, resize_window, move_window, restore_window.

FIX (sweep-4): focus_window now returns a dict instead of a bare string so
callers can detect failure. Adds strict=True exact-match mode. On failure,
returns available_titles so the model can retry with the correct name without
an extra list_windows round-trip.

FIX (Task 5): _hWnd is now re-validated with win32gui.IsWindow() before use.
A cached handle can go stale if the window was closed and re-opened between
calls — the old handle still exists in memory but points to a destroyed window.
We now always re-query via win32gui.FindWindow as the authoritative source and
only fall back to _hWnd if FindWindow returns nothing.

FIX (Task 6): All functions that previously returned bare strings on failure
now return dicts with consistent keys (ok, error/message, title) so every
caller can reliably check success with result.get("ok") or "error" in result.
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
    return {"ok": False, "error": "pygetwindow not available. Run: pip install pygetwindow"}


def _find(title: str):
    """Return list of windows whose title contains `title` (case-insensitive)."""
    return [w for w in gw.getAllWindows() if title.lower() in w.title.lower()]


def _resolve_hwnd(w) -> int | None:
    """
    FIX (Task 5): Safely resolve a live, valid HWND for window `w`.

    pygetwindow caches _hWnd at object-creation time. If a window is closed
    and re-opened (e.g. a browser tab that restarts), _hWnd points to a
    destroyed window handle — IsWindow() returns False and any win32 call
    on it silently fails or raises.

    Resolution order:
      1. Try win32gui.FindWindow(None, w.title) — always authoritative.
      2. Fall back to w._hWnd only if FindWindow returns nothing AND the
         cached handle still passes IsWindow().
      3. Return None if no valid handle found.
    """
    if not HAS_WIN32:
        return None
    # Prefer a fresh lookup by title
    hwnd = win32gui.FindWindow(None, w.title)
    if hwnd and win32gui.IsWindow(hwnd):
        return hwnd
    # Fallback: cached handle, only if still valid
    cached = getattr(w, "_hWnd", None)
    if cached and win32gui.IsWindow(cached):
        return cached
    return None


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
        fg_hwnd = win32gui.GetForegroundWindow()
        fg_tid, _ = win32process.GetWindowThreadProcessId(fg_hwnd)
        target_tid, _ = win32process.GetWindowThreadProcessId(hwnd)
        current_tid = ctypes.windll.kernel32.GetCurrentThreadId()

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


async def focus_window(title: str, strict: bool = False) -> dict:
    """
    Bring a window to the foreground.
    strict=True  → only exact title matches accepted.
    strict=False → case-insensitive substring match (default).
    Returns a dict so callers can detect failure and see available_titles.
    FIX (Task 5): Uses _resolve_hwnd() instead of raw _hWnd to guarantee the
    handle is live before passing it to win32 calls.
    """
    if not HAS_GW:
        return {"ok": False, "error": "pygetwindow not available. Run: pip install pygetwindow"}
    all_windows = [w for w in gw.getAllWindows() if w.title.strip()]
    available_titles = [w.title for w in all_windows]
    if strict:
        matches = [w for w in all_windows if w.title == title]
    else:
        matches = [w for w in all_windows if title.lower() in w.title.lower()]
    if not matches:
        return {
            "ok": False,
            "matched_title": None,
            "available_titles": available_titles,
            "_hint": "Call list_windows to get current titles and retry with the exact string.",
        }
    w = matches[0]
    try:
        if w.isMinimized:
            w.restore()
            await asyncio.sleep(0.2)

        if HAS_WIN32:
            # FIX (Task 5): _resolve_hwnd() re-validates the handle before use
            hwnd = _resolve_hwnd(w)
            if hwnd and _force_foreground(hwnd):
                await asyncio.sleep(0.1)
                return {"ok": True, "focused": w.title, "method": "win32"}

        w.activate()
        return {"ok": True, "focused": w.title, "method": "pygetwindow"}
    except Exception as e:
        return {
            "ok": False,
            "focused": None,
            "error": f"Focus failed for '{w.title}': {e}",
            "available_titles": available_titles,
        }


def get_active_window() -> dict:
    """Return title, position, and size of the currently focused window."""
    if not HAS_GW:
        return _no_gw()
    w = gw.getActiveWindow()
    if not w:
        return {"ok": False, "title": None, "message": "No active window"}
    try:
        return {
            "ok": True,
            "title": w.title,
            "left": w.left,
            "top": w.top,
            "width": w.width,
            "height": w.height,
        }
    except Exception as e:
        return {"ok": False, "title": w.title, "error": str(e)}


async def minimize_window(title: str) -> dict:
    # FIX (Task 6): was returning bare str; now returns dict for consistent error handling
    if not HAS_GW:
        return _no_gw()
    matches = _find(title)
    if not matches:
        return {"ok": False, "error": f"No window matching: '{title}'"}
    try:
        matches[0].minimize()
        return {"ok": True, "action": "minimized", "title": matches[0].title}
    except Exception as e:
        return {"ok": False, "error": f"Minimize failed: {e}"}


async def maximize_window(title: str) -> dict:
    # FIX (Task 6): was returning bare str; now returns dict for consistent error handling
    if not HAS_GW:
        return _no_gw()
    matches = _find(title)
    if not matches:
        return {"ok": False, "error": f"No window matching: '{title}'"}
    try:
        matches[0].maximize()
        return {"ok": True, "action": "maximized", "title": matches[0].title}
    except Exception as e:
        return {"ok": False, "error": f"Maximize failed: {e}"}


async def restore_window(title: str) -> dict:
    """Restore a minimized/maximized window to its normal size."""
    # FIX (Task 6): was returning bare str; now returns dict for consistent error handling
    if not HAS_GW:
        return _no_gw()
    matches = _find(title)
    if not matches:
        return {"ok": False, "error": f"No window matching: '{title}'"}
    try:
        matches[0].restore()
        return {"ok": True, "action": "restored", "title": matches[0].title}
    except Exception as e:
        return {"ok": False, "error": f"Restore failed: {e}"}


async def get_window_rect(title: str) -> dict:
    """Get the exact position and size of a window by title."""
    if not HAS_GW:
        return _no_gw()
    matches = _find(title)
    if not matches:
        return {"ok": False, "error": f"No window matching: '{title}'"}
    w = matches[0]
    try:
        return {
            "ok": True,
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
        return {"ok": False, "error": str(e)}


async def resize_window(title: str, width: int, height: int) -> dict:
    """Resize a window to the given width and height in pixels."""
    # FIX (Task 6): was returning bare str; now returns dict for consistent error handling
    if not HAS_GW:
        return _no_gw()
    matches = _find(title)
    if not matches:
        return {"ok": False, "error": f"No window matching: '{title}'"}
    try:
        matches[0].resizeTo(width, height)
        return {"ok": True, "action": "resized", "title": matches[0].title, "width": width, "height": height}
    except Exception as e:
        return {"ok": False, "error": f"Resize failed: {e}"}


async def move_window(title: str, x: int, y: int) -> dict:
    """Move a window's top-left corner to (x, y)."""
    # FIX (Task 6): was returning bare str; now returns dict for consistent error handling
    if not HAS_GW:
        return _no_gw()
    matches = _find(title)
    if not matches:
        return {"ok": False, "error": f"No window matching: '{title}'"}
    try:
        matches[0].moveTo(x, y)
        return {"ok": True, "action": "moved", "title": matches[0].title, "x": x, "y": y}
    except Exception as e:
        return {"ok": False, "error": f"Move failed: {e}"}
