"""
Mouse & keyboard control via pyautogui.
All functions are async-wrapped so the MCP server stays responsive.
"""
import asyncio, pyautogui

pyautogui.FAILSAFE = True   # Move mouse to top-left corner to abort
pyautogui.PAUSE    = 0.05   # Small pause between actions for stability

async def mouse_move(x: int, y: int) -> str:
    await asyncio.to_thread(pyautogui.moveTo, x, y, duration=0.2)
    return f"Mouse moved to ({x}, {y})"

async def mouse_click(x: int, y: int) -> str:
    await asyncio.to_thread(pyautogui.click, x, y)
    return f"Clicked ({x}, {y})"

async def mouse_double_click(x: int, y: int) -> str:
    await asyncio.to_thread(pyautogui.doubleClick, x, y)
    return f"Double-clicked ({x}, {y})"

async def mouse_right_click(x: int, y: int) -> str:
    await asyncio.to_thread(pyautogui.rightClick, x, y)
    return f"Right-clicked ({x}, {y})"

async def mouse_scroll(x: int, y: int, clicks: int) -> str:
    await asyncio.to_thread(pyautogui.moveTo, x, y, duration=0.1)
    await asyncio.to_thread(pyautogui.scroll, clicks)
    return f"Scrolled {clicks} at ({x}, {y})"

async def keyboard_type(text: str) -> str:
    await asyncio.to_thread(pyautogui.write, text, interval=0.02)
    return f"Typed: {text[:60]}{'...' if len(text)>60 else ''}"

async def keyboard_hotkey(keys: str) -> str:
    """Accept 'ctrl+c', 'alt+f4', 'ctrl+shift+esc' etc."""
    parts = [k.strip() for k in keys.split("+")]
    await asyncio.to_thread(pyautogui.hotkey, *parts)
    return f"Hotkey pressed: {keys}"

async def keyboard_press(key: str) -> str:
    await asyncio.to_thread(pyautogui.press, key)
    return f"Key pressed: {key}"
