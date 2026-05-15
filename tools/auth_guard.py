"""
Authentication guard for user-owned files.
Pops a tkinter dialog asking the user to approve the action.
Agent-owned files bypass this entirely.
Now tracks ownership in data/agent_files.json (separate from memory.json).

FIX (Task 10): _show_auth_dialog previously swallowed all tkinter exceptions
with a bare `except Exception: return False`. This caused silent denials with
no indication of *why* — a headless server, a missing DISPLAY env var, or a
broken tkinter install all produced the same silent False, making it impossible
to distinguish a real user denial from an infrastructure failure.

Now:
  - Exceptions are caught and logged to stderr via the standard `logging` module.
  - _show_auth_dialog returns a named tuple / dict so callers can tell the
    difference between 'user said no' and 'dialog could not be shown'.
  - requires_auth raises a descriptive RuntimeError (not PermissionError) when
    the dialog itself failed, so the agent knows to investigate the environment
    rather than assuming the user denied the request.
"""
import asyncio
import json
import logging
import tkinter as tk
from pathlib import Path
from tkinter import messagebox
from typing import NamedTuple

log = logging.getLogger(__name__)

# Path to the agent-owned file registry
_REGISTRY_PATH = Path(__file__).parent.parent / "data" / "agent_files.json"


class _AuthResult(NamedTuple):
    approved: bool          # True = user approved or agent-owned
    dialog_shown: bool      # False = tkinter failed before user could respond
    denial_reason: str      # Empty string on approval; message on denial/failure


def _load_registry() -> set:
    if _REGISTRY_PATH.exists():
        try:
            return set(json.loads(_REGISTRY_PATH.read_text(encoding="utf-8")))
        except Exception as e:
            log.warning("auth_guard: failed to load agent file registry: %s", e)
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
    - User-owned path         -> show approval dialog.

    Raises:
        PermissionError  — user explicitly denied the request.
        RuntimeError     — dialog could not be shown (tkinter/headless failure).
                           Distinct from PermissionError so the caller knows
                           this is an infrastructure problem, not a user decision.
    """
    p = Path(path)
    if not p.exists():
        result = await func(path, *args)
        register_agent_file(path)
        return result

    if _is_agent_file(path):
        return await func(path, *args)

    auth = await asyncio.to_thread(_show_auth_dialog, path, func.__name__)

    if not auth.dialog_shown:
        # FIX (Task 10): tkinter failed before the user could respond.
        # Raise RuntimeError (not PermissionError) so the agent knows this is
        # an environment problem and not a deliberate denial by the user.
        raise RuntimeError(
            f"auth_guard: could not show approval dialog for '{path}'. "
            f"Reason: {auth.denial_reason}. "
            "Check that a display is available (DISPLAY env var on Linux) "
            "and that tkinter is correctly installed."
        )

    if not auth.approved:
        raise PermissionError(
            f"User denied access to: {path}"
        )

    return await func(path, *args)


def _show_auth_dialog(path: str, action: str) -> _AuthResult:
    """
    FIX (Task 10): Returns _AuthResult instead of bare bool.
    Logs the specific exception before falling back to deny, so the server
    log always shows *why* a dialog failed rather than silently returning False.
    """
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        user_approved = messagebox.askyesno(
            title="Phantom MCP — Permission Required",
            message=(
                f"Phantom wants to {action.upper()}:\n\n"
                f"{path}\n\n"
                "This file was not created by the agent. Allow?"
            ),
            parent=root,
        )
        root.destroy()
        return _AuthResult(
            approved=user_approved,
            dialog_shown=True,
            denial_reason="" if user_approved else "User clicked No.",
        )
    except tk.TclError as e:
        # Most common cause: no DISPLAY on a headless server, or Tcl/Tk not installed.
        msg = f"tkinter TclError (likely headless/no display): {e}"
        log.error("auth_guard: %s", msg)
        return _AuthResult(approved=False, dialog_shown=False, denial_reason=msg)
    except Exception as e:
        # Catch-all for unexpected tkinter failures.
        msg = f"{type(e).__name__}: {e}"
        log.error("auth_guard: unexpected dialog failure — %s", msg)
        return _AuthResult(approved=False, dialog_shown=False, denial_reason=msg)
