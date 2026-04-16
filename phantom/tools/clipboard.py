"""
phantom.tools.clipboard — read/write the system clipboard.

Demonstrates:
  * Two tools from one module (get + set).
  * Strict pydantic schemas — no more silent shape drift.
  * Cross-platform fallback via the legacy tools/clipboard module,
    which already does pyperclip -> PowerShell fallback on Windows. We
    leave Linux/macOS fallbacks for a follow-up PR (xclip/pbpaste).
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from phantom.contracts import ok, fail
from phantom.tools._base import tool


class ClipboardGetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClipboardSetInput(BaseModel):
    text: str = Field(..., description="Text to place on the system clipboard.")

    model_config = ConfigDict(extra="forbid")


@tool(
    "clipboard_get",
    category="clipboard",
    schema=ClipboardGetInput,
    timeout_s=5.0,
)
def clipboard_get() -> dict:
    """
    Read the current contents of the system clipboard as text.

    Returns `{text, chars}`. If the clipboard is empty or the backing
    library is missing, returns an error envelope with a hint.
    """
    from tools.clipboard import clipboard_get as legacy_get

    raw = legacy_get()
    if isinstance(raw, str) and raw.startswith("ERROR"):
        return fail(
            raw,
            hint="Install pyperclip (pip install pyperclip) or ensure PowerShell is reachable.",
            category="external_error",
        )
    return ok({"text": raw or "", "chars": len(raw or "")})


@tool(
    "clipboard_set",
    category="clipboard",
    schema=ClipboardSetInput,
    timeout_s=5.0,
)
def clipboard_set(text: str) -> dict:
    """
    Place `text` on the system clipboard. Returns the number of chars written.
    """
    from tools.clipboard import clipboard_set as legacy_set

    raw = legacy_set(text)
    if isinstance(raw, str) and raw.startswith("ERROR"):
        return fail(
            raw,
            hint="Clipboard backend failed; check pyperclip or PowerShell availability.",
            category="external_error",
        )
    return ok({"chars": len(text), "backend": raw})
