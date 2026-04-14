"""
tools/ocr.py — Screen OCR via Tesseract

Requires:
  pip install pytesseract Pillow mss
  Tesseract binary installed: https://github.com/UB-Mannheim/tesseract/wiki
  (Default install path on Windows: C:\\Program Files\\Tesseract-OCR\\tesseract.exe)

The first call will try to locate tesseract.exe automatically.
If it is not on PATH, set TESSERACT_CMD in your .env or the code below.
"""
from __future__ import annotations
import asyncio
import os
from pathlib import Path
from typing import Optional

_TESSERACT_SEARCHED = False


def _ensure_tesseract() -> None:
    global _TESSERACT_SEARCHED
    if _TESSERACT_SEARCHED:
        return
    _TESSERACT_SEARCHED = True
    import pytesseract  # type: ignore

    # 1. Respect explicit env override
    env_path = os.environ.get("TESSERACT_CMD", "")
    if env_path and Path(env_path).is_file():
        pytesseract.pytesseract.tesseract_cmd = env_path
        return

    # 2. Common default install paths (Windows)
    default_paths = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for p in default_paths:
        if Path(p).is_file():
            pytesseract.pytesseract.tesseract_cmd = p
            return

    # 3. Leave as-is; if it is on PATH, pytesseract finds it automatically


async def ocr_region(region: str = "full", lang: str = "eng") -> dict:
    """
    Capture a screen region and extract text with Tesseract.

    region: 'full' or 'x,y,width,height'
    lang:   Tesseract language code (default 'eng')

    Returns dict with keys:
        text    — extracted text (stripped)
        chars   — character count
        region  — region string used
        error   — present only if something went wrong
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _ocr_sync, region, lang)


def _ocr_sync(region: str, lang: str) -> dict:
    try:
        import mss  # type: ignore
        from PIL import Image  # type: ignore
        import pytesseract  # type: ignore
    except ImportError as e:
        return {"error": f"Missing dependency: {e}. Run: pip install pytesseract Pillow mss"}

    _ensure_tesseract()

    try:
        with mss.mss() as sct:
            if region == "full":
                mon = sct.monitors[1]  # primary monitor
            else:
                parts = [p.strip() for p in region.split(",")]
                if len(parts) != 4:
                    return {"error": "region must be 'full' or 'x,y,width,height'"}
                x, y, w, h = [int(p) for p in parts]
                mon = {"left": x, "top": y, "width": w, "height": h}

            raw = sct.grab(mon)
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

        # Tesseract works best on high-contrast images; scale up small regions
        if img.width < 400:
            scale = max(2, 400 // img.width)
            img = img.resize(
                (img.width * scale, img.height * scale),
                Image.LANCZOS,  # type: ignore[attr-defined]
            )

        text = pytesseract.image_to_string(img, lang=lang)
        text = text.strip()
        return {
            "text": text,
            "chars": len(text),
            "region": region,
        }
    except pytesseract.TesseractNotFoundError:
        return {
            "error": (
                "Tesseract binary not found. Install from "
                "https://github.com/UB-Mannheim/tesseract/wiki "
                "or set the TESSERACT_CMD env variable to its full path."
            )
        }
    except Exception as e:
        return {"error": str(e)}
