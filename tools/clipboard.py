"""
Clipboard read/write using pyperclip.
Falls back to PowerShell if pyperclip is unavailable.
"""
try:
    import pyperclip
    _USE_PYPERCLIP = True
except ImportError:
    _USE_PYPERCLIP = False

import subprocess

def clipboard_get() -> str:
    if _USE_PYPERCLIP:
        try:
            return pyperclip.paste()
        except Exception as e:
            return f"ERROR: {e}"
    # fallback via PowerShell
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", "Get-Clipboard"],
            capture_output=True, text=True, timeout=10
        )
        return result.stdout.strip()
    except Exception as e:
        return f"ERROR (fallback): {e}"

def clipboard_set(text: str) -> str:
    if _USE_PYPERCLIP:
        try:
            pyperclip.copy(text)
            return f"Clipboard set ({len(text)} chars)"
        except Exception as e:
            return f"ERROR: {e}"
    # fallback via PowerShell
    try:
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             f"Set-Clipboard -Value @'
{text}
'@"],
            timeout=10
        )
        return f"Clipboard set via PowerShell ({len(text)} chars)"
    except Exception as e:
        return f"ERROR (fallback): {e}"
