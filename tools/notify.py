"""
Windows desktop toast notifications.
Primary:  win10toast (pip install win10toast)
Fallback: PowerShell New-BurntToastNotification, then a simple Tk messagebox.

The agent uses this to signal goal completion, blocked state, or anything
that needs human attention without halting the work loop.
"""
import asyncio


async def notify_user(title: str, message: str, duration: int = 5) -> str:
    """
    Send a Windows desktop notification.
    title:    Short heading, e.g. 'Phantom — Goal Complete'
    message:  Body text.
    duration: Seconds the toast stays visible (default 5).
    """
    return await asyncio.to_thread(_notify_sync, title, message, duration)


def _notify_sync(title: str, message: str, duration: int) -> str:
    # --- Attempt 1: win10toast ---
    try:
        from win10toast import ToastNotifier
        toaster = ToastNotifier()
        toaster.show_toast(
            title,
            message,
            duration=duration,
            threaded=True,
        )
        return f"Toast sent: {title}"
    except Exception:
        pass

    # --- Attempt 2: PowerShell BurntToast (if installed) ---
    try:
        import subprocess
        ps_cmd = (
            f"New-BurntToastNotification "
            f"-Text '{title.replace(chr(39), '')}', '{message.replace(chr(39), '')}'"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return f"BurntToast notification sent: {title}"
    except Exception:
        pass

    # --- Attempt 3: Tk messagebox (always available on Windows) ---
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        messagebox.showinfo(title=title, message=message, parent=root)
        root.destroy()
        return f"Tk dialog shown: {title}"
    except Exception as e:
        return f"All notification methods failed: {e}"
