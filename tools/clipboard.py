"""
Clipboard read/write.
Primary: pyperclip. Fallback: PowerShell.
Fixed: heredoc bug in PS fallback that broke when text contained quote sequences.
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
            return f"ERROR (pyperclip): {e}"
    # PowerShell fallback
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", "Get-Clipboard"],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip()
    except Exception as e:
        return f"ERROR (PS fallback): {e}"


def clipboard_set(text: str) -> str:
    if _USE_PYPERCLIP:
        try:
            pyperclip.copy(text)
            return f"Clipboard set ({len(text)} chars)"
        except Exception as e:
            return f"ERROR (pyperclip): {e}"
    # PowerShell fallback — use stdin pipe to avoid shell-escaping issues entirely
    try:
        # Write text to stdin of a PS process that reads it back as a string
        ps_cmd = "$input | Set-Clipboard"
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            input=text,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return f"Clipboard set via PS ({len(text)} chars)"
        return f"ERROR (PS fallback): {result.stderr.strip()}"
    except Exception as e:
        return f"ERROR (PS fallback): {e}"
