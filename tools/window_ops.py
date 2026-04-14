"""
Window management using pygetwindow.
"""
import asyncio
try:
    import pygetwindow as gw
    HAS_GW = True
except ImportError:
    HAS_GW = False

def _no_gw():
    return "pygetwindow not available — run: pip install pygetwindow"

async def list_windows() -> list:
    if not HAS_GW: return [_no_gw()]
    return [w.title for w in gw.getAllWindows() if w.title.strip()]

async def focus_window(title: str) -> str:
    if not HAS_GW: return _no_gw()
    matches = [w for w in gw.getAllWindows() if title.lower() in w.title.lower()]
    if not matches:
        return f"No window found matching: {title}"
    matches[0].activate()
    return f"Focused: {matches[0].title}"

def get_active_window() -> str:
    if not HAS_GW: return _no_gw()
    w = gw.getActiveWindow()
    return w.title if w else "No active window"

async def minimize_window(title: str) -> str:
    if not HAS_GW: return _no_gw()
    matches = [w for w in gw.getAllWindows() if title.lower() in w.title.lower()]
    if not matches: return f"No window: {title}"
    matches[0].minimize()
    return f"Minimized: {matches[0].title}"

async def maximize_window(title: str) -> str:
    if not HAS_GW: return _no_gw()
    matches = [w for w in gw.getAllWindows() if title.lower() in w.title.lower()]
    if not matches: return f"No window: {title}"
    matches[0].maximize()
    return f"Maximized: {matches[0].title}"
