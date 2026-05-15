"""
Screen capture using mss.
Downscales to max 1280px wide and converts to JPEG (quality 60) before base64-encoding.
This keeps screenshot tokens under ~4000 so Gemma 4 E4B never blows its context window.

Full-res PNG at 1920x1080 = ~37,000 tokens   <-- was crashing
Resized JPEG at 1280x720, q60 = ~2,500 tokens <-- safe

FIX: take_screenshot_hires region parsing now has try/except to return error dict
instead of crashing the tool call on a malformed region string.

Pass 7 — OCR + Screen Monitoring:
  * ocr_region(region, lang) — runs pytesseract on a screen region and returns
    the extracted text. Falls back gracefully if tesseract is not installed.
  * find_text_on_screen(needle, region, case_sensitive) — runs OCR then searches
    for a string. Returns found=True/False + the bounding box of the first match
    as (x, y, w, h) screen coordinates so the model can click it directly.
  * wait_for_text(needle, region, timeout_s, poll_s) — polls OCR until the needle
    appears on screen or the timeout expires. Returns when found.
  * watch_region(region, change_threshold, timeout_s, poll_s) — captures a
    baseline screenshot, then polls until the region changes visually by more
    than a pixel-difference threshold. Returns when the screen has changed.
    Used to detect loading spinners completing, dialogs appearing/disappearing,
    or any visual transition without requiring OCR.

Dependencies:
  * pytesseract + Pillow (already present) for OCR.
  * numpy for pixel-diff monitoring (lightweight, usually already installed).
  * Tesseract-OCR binary must be on PATH for OCR tools to work.
    If missing, OCR tools return {ok: false, error: "tesseract not found", ...}
    with a clear install hint rather than raising.
"""
import asyncio
import base64
import io

from mss import mss as MSS
import pyautogui

MAX_WIDTH = 1280
JPEG_QUALITY = 60


def _parse_region(region: str, sct):
    """Parse a region string 'x,y,w,h' or return the full monitor."""
    if region == "full" or not region:
        return sct.monitors[0]
    try:
        x, y, w, h = map(int, region.split(","))
        return {"top": y, "left": x, "width": w, "height": h}
    except (ValueError, TypeError):
        return sct.monitors[0]  # fall back to full screen on bad input


def _region_to_dict(region: str) -> dict | None:
    """Parse 'x,y,w,h' or 'full' to a plain dict. Returns None for 'full'."""
    if region == "full" or not region:
        return None
    try:
        x, y, w, h = map(int, region.split(","))
        return {"left": x, "top": y, "width": w, "height": h}
    except (ValueError, TypeError):
        return None


def _grab_pil(region: str):
    """Grab a PIL.Image.Image of the requested region."""
    from PIL import Image
    with MSS() as sct:
        monitor = _parse_region(region, sct)
        raw = sct.grab(monitor)
    return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


# ---------------------------------------------------------------------------
# Screenshot
# ---------------------------------------------------------------------------

async def take_screenshot(region: str = "full") -> str:
    """Capture screen, resize, compress to JPEG, return base64 string."""
    def _capture():
        img = _grab_pil(region)

        orig_w, orig_h = img.size
        if orig_w > MAX_WIDTH:
            ratio = MAX_WIDTH / orig_w
            new_h = int(orig_h * ratio)
            img = img.resize((MAX_WIDTH, new_h), Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        return base64.b64encode(buf.getvalue()).decode()

    from PIL import Image
    return await asyncio.to_thread(_capture)


async def take_screenshot_hires(region: str = "full") -> str:
    """
    Full-resolution PNG screenshot. WARNING: ~37k tokens on a 1080p screen.
    Only use when you need to read very small text. Requires context >= 40000.
    FIX: region parsing now uses shared _parse_region() with error handling.
    """
    def _capture():
        img = _grab_pil(region)
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return base64.b64encode(buf.getvalue()).decode()
    from PIL import Image
    return await asyncio.to_thread(_capture)


def get_screen_info() -> dict:
    size = pyautogui.size()
    return {
        "width": size.width,
        "height": size.height,
        "screenshot_max_width": MAX_WIDTH,
        "screenshot_jpeg_quality": JPEG_QUALITY,
        "note": "Use coordinates within width/height bounds for mouse actions. Screenshots are downscaled to save context tokens."
    }


# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------

def _tesseract_available() -> bool:
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


_TESSERACT_HINT = (
    "Tesseract-OCR is not installed or not on PATH. "
    "Install it from https://github.com/UB-Mannheim/tesseract/wiki (Windows) "
    "or 'sudo apt install tesseract-ocr' (Linux), then restart the MCP server."
)


def _ocr_pil_sync(img, lang: str = "eng") -> str:
    """Run tesseract on a PIL image and return the extracted text."""
    import pytesseract
    # PSM 6 = assume a uniform block of text (best general default)
    config = f"--psm 6 -l {lang}"
    return pytesseract.image_to_string(img, config=config)


def _ocr_pil_data_sync(img, lang: str = "eng") -> dict:
    """
    Run tesseract and return word-level bounding boxes.
    Returns pytesseract image_to_data as a list of word dicts.
    """
    import pytesseract
    import pandas as pd
    config = f"--psm 6 -l {lang}"
    df = pytesseract.image_to_data(img, config=config, output_type=pytesseract.Output.DATAFRAME)
    # Filter to rows that have actual text
    df = df[df["text"].notna() & (df["text"].str.strip() != "")]
    return df[["text", "left", "top", "width", "height", "conf"]].to_dict(orient="records")


async def ocr_region(region: str = "full", lang: str = "eng") -> dict:
    """
    OCR a screen region and return the full extracted text.
    region: 'full' or 'x,y,w,h'
    lang: tesseract language code (default 'eng')
    """
    if not await asyncio.to_thread(_tesseract_available):
        return {"ok": False, "error": "tesseract_not_found", "hint": _TESSERACT_HINT}
    img = await asyncio.to_thread(_grab_pil, region)
    text = await asyncio.to_thread(_ocr_pil_sync, img, lang)
    return {
        "ok": True,
        "region": region,
        "text": text.strip(),
        "char_count": len(text.strip()),
    }


async def find_text_on_screen(
    needle: str,
    region: str = "full",
    case_sensitive: bool = False,
    lang: str = "eng",
) -> dict:
    """
    OCR the screen region and find the needle string.
    Returns found=True/False.
    If found, also returns the first match's screen coordinates (x, y, w, h)
    so the model can pass them directly to desktop_click.
    """
    if not await asyncio.to_thread(_tesseract_available):
        return {"ok": False, "error": "tesseract_not_found", "hint": _TESSERACT_HINT}

    img = await asyncio.to_thread(_grab_pil, region)
    words = await asyncio.to_thread(_ocr_pil_data_sync, img, lang)

    region_offset = _region_to_dict(region)
    ox = region_offset["left"] if region_offset else 0
    oy = region_offset["top"] if region_offset else 0

    # Reconstruct line text for multi-word searches
    # Build a flat string to search, track word-level positions
    compare = needle if case_sensitive else needle.lower()

    # Single-word fast path
    for w in words:
        word_text = w["text"] if case_sensitive else w["text"].lower()
        if compare in word_text:
            return {
                "ok": True,
                "found": True,
                "needle": needle,
                "match": w["text"],
                "x": w["left"] + ox,
                "y": w["top"] + oy,
                "width": w["width"],
                "height": w["height"],
                "click_x": w["left"] + ox + w["width"] // 2,
                "click_y": w["top"] + oy + w["height"] // 2,
            }

    # Multi-word fallback: join all words and check substring
    all_text = " ".join(w["text"] for w in words)
    all_cmp = all_text if case_sensitive else all_text.lower()
    found_in_text = compare in all_cmp

    return {
        "ok": True,
        "found": found_in_text,
        "needle": needle,
        "full_ocr_text": all_text if not found_in_text else None,
        "note": "multi-word match detected but exact bounding box unavailable" if found_in_text else None,
    }


async def wait_for_text(
    needle: str,
    region: str = "full",
    timeout_s: float = 15.0,
    poll_s: float = 1.0,
    case_sensitive: bool = False,
    lang: str = "eng",
) -> dict:
    """
    Poll OCR until `needle` appears in the region or timeout expires.
    Returns when found, or returns found=False after timeout.
    """
    if not await asyncio.to_thread(_tesseract_available):
        return {"ok": False, "error": "tesseract_not_found", "hint": _TESSERACT_HINT}

    import time
    deadline = time.monotonic() + timeout_s
    attempts = 0

    while time.monotonic() < deadline:
        attempts += 1
        result = await find_text_on_screen(needle, region=region, case_sensitive=case_sensitive, lang=lang)
        if result.get("found"):
            result["attempts"] = attempts
            result["waited_s"] = round(time.monotonic() - (deadline - timeout_s), 2)
            return result
        remaining = deadline - time.monotonic()
        await asyncio.sleep(min(poll_s, max(0, remaining)))

    return {
        "ok": True,
        "found": False,
        "needle": needle,
        "timed_out": True,
        "timeout_s": timeout_s,
        "attempts": attempts,
    }


async def watch_region_for_change(
    region: str = "full",
    change_threshold: float = 0.02,
    timeout_s: float = 15.0,
    poll_s: float = 0.5,
) -> dict:
    """
    Capture a baseline image of the region, then poll until it changes
    by more than `change_threshold` (fraction of pixels that differ).

    change_threshold=0.02 means 2% of pixels must change — enough to
    detect a dialog appearing or a spinner disappearing without false-positives
    from cursor blink or minor anti-aliasing differences.

    Returns {changed: True, changed_fraction: 0.07, waited_s: 2.1} when
    a visual change is detected, or {changed: False, timed_out: True} otherwise.
    """
    try:
        import numpy as np
    except ImportError:
        return {
            "ok": False,
            "error": "numpy_not_installed",
            "hint": "Run: pip install numpy",
        }

    import time

    def _grab_array(r: str):
        img = _grab_pil(r)
        return np.array(img)

    baseline = await asyncio.to_thread(_grab_array, region)
    deadline = time.monotonic() + timeout_s
    start = time.monotonic()
    attempts = 0

    while time.monotonic() < deadline:
        await asyncio.sleep(poll_s)
        attempts += 1
        current = await asyncio.to_thread(_grab_array, region)
        if current.shape != baseline.shape:
            # Region size changed (e.g. window resized) — count as a change
            return {
                "ok": True,
                "changed": True,
                "reason": "region_shape_changed",
                "waited_s": round(time.monotonic() - start, 2),
                "attempts": attempts,
            }
        diff = np.mean(np.abs(current.astype(int) - baseline.astype(int)) > 10)
        if diff >= change_threshold:
            return {
                "ok": True,
                "changed": True,
                "changed_fraction": round(float(diff), 4),
                "waited_s": round(time.monotonic() - start, 2),
                "attempts": attempts,
            }

    return {
        "ok": True,
        "changed": False,
        "timed_out": True,
        "timeout_s": timeout_s,
        "attempts": attempts,
    }
