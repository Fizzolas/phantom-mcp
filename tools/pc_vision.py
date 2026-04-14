"""
Screen capture — uses mss.
Downscales to max 1280px wide and converts to JPEG (quality 60) before base64-encoding.
This keeps screenshot tokens under ~4000 so Gemma 4 E4B never blows its context window.

Full-res PNG at 1920x1080 = ~37,000 tokens   <-- was crashing
Resized JPEG at 1280x720, q60 = ~2,500 tokens <-- safe
"""
import asyncio
import base64
import io

from mss import mss as MSS
import pyautogui

# Max width for screenshots sent to the LLM.
# Increase if you want more detail (but more tokens).
# At 1280 a 1920x1080 screen becomes 1280x720 (~2-4k tokens).
# At 960 it becomes 960x540 (~1.5-2.5k tokens) — use this if still hitting limits.
MAX_WIDTH = 1280
JPEG_QUALITY = 60  # 60 is readable; lower = fewer tokens but blurrier text


async def take_screenshot(region: str = "full") -> str:
    """Capture screen, resize, compress to JPEG, return base64 string."""
    def _capture():
        with MSS() as sct:
            if region == "full":
                monitor = sct.monitors[0]  # index 0 = combined virtual screen
            else:
                try:
                    x, y, w, h = map(int, region.split(","))
                    monitor = {"top": y, "left": x, "width": w, "height": h}
                except ValueError:
                    monitor = sct.monitors[0]

            raw = sct.grab(monitor)

        from PIL import Image
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

        # --- Resize if wider than MAX_WIDTH ---
        orig_w, orig_h = img.size
        if orig_w > MAX_WIDTH:
            ratio = MAX_WIDTH / orig_w
            new_h = int(orig_h * ratio)
            img = img.resize((MAX_WIDTH, new_h), Image.LANCZOS)

        # --- Encode as JPEG (much smaller than PNG) ---
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        return base64.b64encode(buf.getvalue()).decode()

    return await asyncio.to_thread(_capture)


async def take_screenshot_hires(region: str = "full") -> str:
    """Full-resolution PNG screenshot. WARNING: ~37k tokens on a 1080p screen.
    Only use when you need to read tiny text. Requires context >= 40000."""
    def _capture():
        with MSS() as sct:
            if region == "full":
                monitor = sct.monitors[0]
            else:
                x, y, w, h = map(int, region.split(","))
                monitor = {"top": y, "left": x, "width": w, "height": h}
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
