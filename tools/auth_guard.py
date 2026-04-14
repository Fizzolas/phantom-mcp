"""
Authentication guard for user-owned files.
Pops a tkinter dialog asking the user to approve the action.
Agent-owned files bypass this entirely.
"""
import asyncio, tkinter as tk
from tkinter import messagebox
from pathlib import Path

def _is_agent_file(path: str) -> bool:
    from tools.file_ops import _is_agent_file as _check
    return _check(path)

async def requires_auth(func, path: str, *args):
    """
    Wrap a file operation with ownership check.
    - Agent-owned path  → runs immediately, no prompt.
    - New path (not yet on disk) → assume agent creating it, runs immediately.
    - User-owned path   → shows approval dialog. Raises PermissionError on deny.
    """
    p = Path(path)
    if not p.exists():
        return await func(path, *args)
    if _is_agent_file(path):
        return await func(path, *args)
    approved = await asyncio.to_thread(_show_auth_dialog, path, func.__name__)
    if not approved:
        raise PermissionError(f"User denied access to: {path}")
    return await func(path, *args)

def _show_auth_dialog(path: str, action: str) -> bool:
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    result = messagebox.askyesno(
        title="Phantom MCP — Permission Required",
        message=(
            f"The AI agent wants to {action.upper()} a file you own:\n\n"
            f"{path}\n\n"
            "Allow this action?"
        ),
        parent=root,
    )
    root.destroy()
    return result
