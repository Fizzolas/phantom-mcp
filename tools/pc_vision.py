"""
Screen capture using mss.
Downscales to max 1280px wide and converts to JPEG (quality 60) before base64-encoding.
This keeps screenshot tokens under ~4000 so Gemma 4 E4B never blows its context window.

Full-res PNG at 1920x1080 = ~37,000 tokens   <-- was crashing
Resized JPEG at 1280x720, q60 = ~2,500 tokens <-- safe

FIX: take_screenshot_hires region parsing now has try/except to return error dict
instead of crashing the tool call on a malformed region string.
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


async def take_screenshot(region: str = "full") -> str:
    """Capture screen, resize, compress to JPEG, return base64 string."""
    def _capture():
        with MSS() as sct:
            monitor = _parse_region(region, sct)
            raw = sct.grab(monitor)

        from PIL import Image
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

        orig_w, orig_h = img.size
        if orig_w > MAX_WIDTH:
            ratio = MAX_WIDTH / orig_w
            new_h = int(orig_h * ratio)
            img = img.resize((MAX_WIDTH, new_h), Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        return base64.b64encode(buf.getvalue()).decode()

    return await asyncio.to_thread(_capture)


async def take_screenshot_hires(region: str = "full") -> str:
    """
    Full-resolution PNG screenshot. WARNING: ~37k tokens on a 1080p screen.
    Only use when you need to read very small text. Requires context >= 40000.
    FIX: region parsing now uses shared _parse_region() with error handling.
    """
    def _capture():
        with MSS() as sct:
            monitor = _parse_region(region, sct)
            raw = sct.grab(monitor)
        from PIL import Image
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return base64.b64encode(buf.getvalue()).decode()
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
