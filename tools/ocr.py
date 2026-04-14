"""
OCR tool — read text from a screen region using Tesseract.

Why this exists:
  Gemma 4B reads screenshot images, but JPEG compression + downscaling makes
  small UI text (terminal output, file dialog paths, error codes, menu items)
  unreliable to read visually. OCR converts those pixels to a clean string the
  model can parse exactly.

Requires: pip install pytesseract pillow
  AND Tesseract installed at C:\\Program Files\\Tesseract-OCR\\tesseract.exe
  Download: https://github.com/UB-Mannheim/tesseract/wiki

Usage:
  region='full'           -> OCR the entire screen
  region='x,y,width,height' -> OCR a sub-region (recommended — faster and more accurate)
"""
import asyncio
import io
from pathlib import Path

try:
    import pytesseract
    from PIL import Image
    from mss import mss as MSS
    HAS_OCR = True

    # Auto-detect Tesseract on Windows default install path
    _default_tess = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    if _default_tess.exists():
        pytesseract.pytesseract.tesseract_cmd = str(_default_tess)
except ImportError:
    HAS_OCR = False


def _parse_region(region: str, sct):
    """Parse 'x,y,w,h' or return full monitor."""
    if region == "full" or not region:
        return sct.monitors[0]
    try:
        x, y, w, h = map(int, region.split(","))
        return {"top": y, "left": x, "width": w, "height": h}
    except (ValueError, TypeError):
        return sct.monitors[0]


async def ocr_region(region: str = "full", lang: str = "eng") -> dict:
    """
    Capture a screen region and return the OCR text.

    Args:
        region: 'full' or 'x,y,width,height'
        lang:   Tesseract language code (default 'eng')

    Returns:
        {'text': <extracted text>, 'char_count': int, 'region': str}
        or {'error': <message>} if Tesseract is unavailable.
    """
    if not HAS_OCR:
        return {
            "error": (
                "pytesseract or Pillow not installed. "
                "Run: pip install pytesseract pillow\n"
                "Also install Tesseract: "
                "https://github.com/UB-Mannheim/tesseract/wiki"
            )
        }

    def _run():
        with MSS() as sct:
            monitor = _parse_region(region, sct)
            raw = sct.grab(monitor)

        # Convert raw mss screenshot to PIL image
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

        # Upscale small regions 2x — Tesseract accuracy improves significantly
        w, h = img.size
        if w < 800:
            img = img.resize((w * 2, h * 2), Image.LANCZOS)

        # Run OCR
        text = pytesseract.image_to_string(img, lang=lang)
        return text.strip()

    try:
        text = await asyncio.to_thread(_run)
        return {
            "text": text,
            "char_count": len(text),
            "region": region,
            "note": "Empty result may mean the region contains no text or Tesseract path is wrong."
            if not text else "",
        }
    except Exception as e:
        return {"error": str(e)}
