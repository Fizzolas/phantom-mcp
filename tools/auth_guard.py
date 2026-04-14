"""
Authentication guard for user-owned files.
Pops a tkinter dialog asking the user to approve the action.
Agent-owned files bypass this entirely.
Now tracks ownership in data/agent_files.json (separate from memory.json).
"""
import asyncio
import json
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

# Path to the agent-owned file registry
_REGISTRY_PATH = Path(__file__).parent.parent / "data" / "agent_files.json"


def _load_registry() -> set:
    if _REGISTRY_PATH.exists():
        try:
            return set(json.loads(_REGISTRY_PATH.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()


def _save_registry(paths: set):
    _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _REGISTRY_PATH.write_text(
        json.dumps(sorted(paths), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def register_agent_file(path: str):
    """Mark a path as agent-created so future edits skip the auth dialog."""
    registry = _load_registry()
    registry.add(str(Path(path).resolve()))
    _save_registry(registry)


def _is_agent_file(path: str) -> bool:
    registry = _load_registry()
    return str(Path(path).resolve()) in registry


async def requires_auth(func, path: str, *args):
    """
    Wrap a file operation with ownership check.
    - Path doesn't exist yet  -> agent creating it; register and proceed.
    - Agent-owned path        -> proceed immediately.
    - User-owned path         -> show approval dialog; raise PermissionError on deny.
    """
    p = Path(path)
    if not p.exists():
        # New file — agent is creating it, register ownership
        result = await func(path, *args)
        register_agent_file(path)
        return result

    if _is_agent_file(path):
        return await func(path, *args)

    approved = await asyncio.to_thread(_show_auth_dialog, path, func.__name__)
    if not approved:
        raise PermissionError(f"User denied access to: {path}")
    return await func(path, *args)


def _show_auth_dialog(path: str, action: str) -> bool:
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        result = messagebox.askyesno(
            title="Phantom MCP — Permission Required",
            message=(
                f"Phantom wants to {action.upper()}:\n\n"
                f"{path}\n\n"
                "This file was not created by the agent. Allow?"
            ),
            parent=root,
        )
        root.destroy()
        return result
    except Exception:
        # If tkinter fails (headless), default to deny for safety
        return False
