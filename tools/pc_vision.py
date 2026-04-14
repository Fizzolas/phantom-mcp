"""
Screen capture — uses mss (already installed on FizzBeast).
Returns base64-encoded PNG so LM Studio can display it.
"""
import asyncio, base64, io
from mss import mss as MSS
import pyautogui

async def take_screenshot(region: str = "full") -> str:
    """Capture screen. region='full' or 'x,y,w,h'."""
    def _capture():
        with MSS() as sct:
            if region == "full":
                monitor = sct.monitors[0]
            else:
                x, y, w, h = map(int, region.split(","))
                monitor = {"top": y, "left": x, "width": w, "height": h}
            img = sct.grab(monitor)
            from PIL import Image
            pil = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
            buf = io.BytesIO()
            pil.save(buf, format="PNG", optimize=True)
            return base64.b64encode(buf.getvalue()).decode()
    return await asyncio.to_thread(_capture)

def get_screen_info() -> dict:
    size = pyautogui.size()
    return {"width": size.width, "height": size.height,
            "note": "Use coordinates within these bounds for mouse actions."}
