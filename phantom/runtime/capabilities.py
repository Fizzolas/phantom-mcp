"""
Capability probe — figures out at boot what the host can actually do.

Returns a set of capability strings that the ToolRegistry uses to gate
tools whose `needs=(...)` aren't met. Nothing here raises; a probe
failure just means the capability is absent.

Capability keys (additive over time):
  desktop       — a user-facing desktop session exists (DISPLAY, WAYLAND, Windows, macOS)
  display       — alias for desktop (legacy; kept for back-compat)
  playwright    — playwright importable AND chromium browser installed
  tesseract     — tesseract binary on PATH or TESSERACT_CMD env var
  ffmpeg        — ffmpeg binary on PATH
  yt_dlp        — yt-dlp binary on PATH
  pyautogui     — pyautogui importable (desktop automation)
  mss           — mss importable (fast screen capture)
  pynput        — pynput importable (keyboard/mouse input)
  psutil        — psutil importable (process/system info)
  numpy         — numpy importable (needed by pixel-diff and vision tools)
  pillow        — PIL/Pillow importable (image processing)
  pdfminer      — pdfminer.six importable (PDF reading)
  docx          — python-docx importable (Word file reading)
  qrcode        — qrcode importable (QR generation)
  boto3         — boto3 importable (AWS S3)
  feedparser    — feedparser importable (RSS/Atom feeds)
  os:windows / os:linux / os:darwin  — current platform

Phase 3 additions:
  - All Python-package capabilities are now probed via _has_package() which
    catches ImportError cleanly and logs at debug level (not warning) so
    expected-missing packages don't spam the log on every boot.
  - _has_desktop() on Windows no longer unconditionally returns True.
    It checks for a real interactive session (not a Windows Service or
    headless CI runner) by testing ctypes.windll.user32.GetDesktopWindow().
  - _has_playwright() validates that at least one Chromium variant is
    actually installed in the browsers path, not just that the directory
    exists and is non-empty.
  - probe_capabilities() logs a one-line summary at INFO level so it's
    easy to see at boot what's available and what's missing.
"""
from __future__ import annotations

import logging
import os
import platform
import shutil
import sys
from pathlib import Path

log = logging.getLogger(__name__)

_WINDOWS = sys.platform.startswith("win")
_MAC = sys.platform == "darwin"


# ------------------------------------------------------------------
# Desktop / display session
# ------------------------------------------------------------------

def _has_desktop() -> bool:
    """
    Return True only when a real interactive desktop session exists.

    Phase 3: Windows now checks for an actual desktop window rather than
    unconditionally returning True. This prevents desktop tools from being
    advertised inside Windows Services, headless CI, or Docker containers
    running Windows images without a display.
    """
    if _WINDOWS:
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetDesktopWindow()
            return hwnd != 0
        except Exception:
            # ctypes not available or call failed — assume desktop present
            # on Windows to preserve backward compatibility.
            return True
    if _MAC:
        # macOS: check for a window server session.
        # TERM_PROGRAM is set in interactive Terminal sessions; DISPLAY may
        # be set by XQuartz. Either is sufficient.
        return bool(
            os.environ.get("DISPLAY")
            or os.environ.get("TERM_PROGRAM")
            or Path("/tmp/.X11-unix").exists()
        )
    # Linux — need DISPLAY or WAYLAND_DISPLAY
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


# ------------------------------------------------------------------
# Binary probes
# ------------------------------------------------------------------

def _has_binary(name: str) -> bool:
    return shutil.which(name) is not None


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


# ------------------------------------------------------------------
# Python package probes
# ------------------------------------------------------------------

def _has_package(import_name: str) -> bool:
    """
    Try to import `import_name`. Returns True if successful, False if
    ImportError or any other exception. Logs at DEBUG level only —
    missing optional packages are expected and should not spam the log.
    """
    try:
        __import__(import_name)
        return True
    except Exception as e:
        log.debug("optional package %r not available: %s", import_name, e)
        return False


def _has_playwright() -> bool:
    """
    Playwright is usable only when:
      1. The `playwright` package is importable.
      2. At least one Chromium browser variant is installed under the
         playwright browsers directory.

    Phase 3: checking that the browsers dir is non-empty is not enough —
    it could contain only Firefox or WebKit. We now glob for a 'chromium*'
    subdirectory specifically, since that's what web tools use.
    """
    if not _has_package("playwright"):
        return False

    # Collect candidate browser roots.
    roots: list[Path] = []
    env_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    if env_path:
        roots.append(Path(env_path))
    roots.append(Path.home() / ".cache" / "ms-playwright")
    if _WINDOWS:
        userprofile = os.environ.get("USERPROFILE", "")
        if userprofile:
            roots.append(Path(userprofile) / "AppData" / "Local" / "ms-playwright")

    for root in roots:
        if not root.exists():
            continue
        # Look for a chromium* directory anywhere one level deep.
        if any(root.glob("chromium*")):
            return True

    log.debug(
        "playwright package found but no Chromium browser installed. "
        "Run: python -m playwright install chromium"
    )
    return False


# ------------------------------------------------------------------
# Main probe
# ------------------------------------------------------------------

def probe_capabilities() -> set[str]:
    """
    Run all probes and return the set of satisfied capability strings.
    Logs a one-line summary at INFO so it's easy to see at boot.
    """
    caps: set[str] = set()

    # --- Desktop / display session ---
    if _has_desktop():
        caps.add("desktop")
        caps.add("display")  # legacy alias

    # --- Browser automation ---
    if _has_playwright():
        caps.add("playwright")

    # --- OCR / vision binaries ---
    if _has_tesseract():
        caps.add("tesseract")
    if _has_binary("ffmpeg"):
        caps.add("ffmpeg")
    if _has_binary("yt-dlp"):
        caps.add("yt_dlp")

    # --- Python packages (desktop automation) ---
    if _has_package("pyautogui"):
        caps.add("pyautogui")
    if _has_package("mss"):
        caps.add("mss")
    if _has_package("pynput"):
        caps.add("pynput")

    # --- Python packages (system / utility) ---
    if _has_package("psutil"):
        caps.add("psutil")
    if _has_package("numpy"):
        caps.add("numpy")
    if _has_package("PIL"):  # Pillow
        caps.add("pillow")

    # --- Python packages (document / media) ---
    if _has_package("pdfminer"):
        caps.add("pdfminer")
    if _has_package("docx"):
        caps.add("docx")
    if _has_package("qrcode"):
        caps.add("qrcode")
    if _has_package("boto3"):
        caps.add("boto3")
    if _has_package("feedparser"):
        caps.add("feedparser")

    # --- OS tag ---
    caps.add(f"os:{platform.system().lower()}")

    # Boot summary — one line, easy to grep in logs.
    present = sorted(c for c in caps if not c.startswith("os:"))
    missing_key = [c for c in (
        "desktop", "playwright", "tesseract", "pyautogui", "mss", "pynput",
        "psutil", "numpy", "pillow",
    ) if c not in caps]
    log.info(
        "capabilities: present=%s  missing=%s  os=%s",
        present,
        missing_key or "none",
        platform.system(),
    )
    return caps
