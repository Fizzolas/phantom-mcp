"""
Capability probe — figures out at boot what the host can actually do.

Returns a set of capability strings that the ToolRegistry uses to gate
tools whose `needs=(...)` aren't met. Nothing here raises; a probe
failure just means the capability is absent.

Capability keys (additive over time):
  desktop     — a user-facing desktop session exists (DISPLAY, WAYLAND, or Windows/macOS)
  playwright  — playwright is importable AND its browsers appear installed
  tesseract   — tesseract binary is on PATH (or TESSERACT_CMD points to it)
  ffmpeg      — ffmpeg binary on PATH
  yt_dlp      — yt-dlp binary on PATH
"""
from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path

_WINDOWS = sys.platform.startswith("win")
_MAC = sys.platform == "darwin"


def _has_desktop() -> bool:
    if _WINDOWS:
        return True  # Windows user sessions always have a desktop
    if _MAC:
        return True
    # Linux — need DISPLAY or WAYLAND_DISPLAY
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _has_playwright() -> bool:
    try:
        import playwright  # type: ignore # noqa: F401
    except Exception:
        return False
    # Playwright can be importable but browsers not installed.
    # Probe the default install path.
    home = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or Path.home() / ".cache" / "ms-playwright")
    if home.exists() and any(home.iterdir()):
        return True
    # Windows default
    if _WINDOWS:
        local = Path(os.environ.get("USERPROFILE", "")) / "AppData" / "Local" / "ms-playwright"
        if local.exists() and any(local.iterdir()):
            return True
    return False


def _has_tesseract() -> bool:
    env_cmd = os.environ.get("TESSERACT_CMD", "")
    if env_cmd and Path(env_cmd).is_file():
        return True
    if shutil.which("tesseract"):
        return True
    if _WINDOWS:
        for candidate in (
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ):
            if Path(candidate).is_file():
                return True
    return False


def _has_binary(name: str) -> bool:
    return shutil.which(name) is not None


def probe_capabilities() -> set[str]:
    caps: set[str] = set()
    if _has_desktop():
        caps.add("desktop")
        # OCR/vision tools also need a display
        caps.add("display")
    if _has_playwright():
        caps.add("playwright")
    if _has_tesseract():
        caps.add("tesseract")
    if _has_binary("ffmpeg"):
        caps.add("ffmpeg")
    if _has_binary("yt-dlp"):
        caps.add("yt_dlp")

    # Expose OS as a capability so future tools can gate on it cleanly.
    caps.add(f"os:{platform.system().lower()}")
    return caps
