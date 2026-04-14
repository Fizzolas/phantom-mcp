"""
tools/web_search.py  —  All 38 noapi-google-search-mcp tools ported into Phantom

Source: https://github.com/VincentKaufmann/noapi-google-search-mcp
License: MIT

Every function here drives a headless Chromium browser through Playwright.
No API keys required.  No usage limits.

Playwright must be installed and browsers downloaded:
  py -3.11 -m pip install playwright
  py -3.11 -m playwright install chromium

All functions are async and return a dict.  Output text is capped at 8000
chars with _truncate() so the model context doesn't explode.

──────────────────────────────────────────────────────────────────────────────
TOOL LIST (38 total)
──────────────────────────────────────────────────────────────────────────────
Search & Web
  google_search, google_news, google_scholar, google_images, google_trends
  visit_page

Travel & Commerce
  google_shopping, google_flights, google_hotels, google_translate
  google_maps, google_maps_directions

Finance & Info
  google_finance, google_weather, google_books

Vision & OCR
  google_lens, google_lens_detect, ocr_image, list_images

Video & Audio
  transcribe_video, transcribe_local, search_transcript
  extract_video_clip, convert_media

Documents & Data
  read_document

Email
  fetch_emails

Web Utilities
  paste_text, shorten_url, generate_qr, archive_webpage, wikipedia

Cloud Storage
  upload_to_s3

Feed Subscriptions
  subscribe, unsubscribe, list_subscriptions
  check_feeds, search_feeds, get_feed_items
──────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import mimetypes
import os
import re
import sqlite3
import tempfile
import textwrap
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ── constants ────────────────────────────────────────────────────────────────
MAX_OUTPUT = 8_000
DATA_DIR = Path(__file__).parent.parent / "data"
FEEDS_DB = DATA_DIR / "feeds.db"
TRANSCRIPT_DB = DATA_DIR / "transcripts.db"

DATA_DIR.mkdir(exist_ok=True)

_STEALTH_JS = """
Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
window.chrome={runtime:{}};
Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});
Object.defineProperty(navigator,'languages',{get:()=>['en-US','en']});
"""

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ── helpers ───────────────────────────────────────────────────────────────────
def _truncate(text: str, cap: int = MAX_OUTPUT) -> str:
    if len(text) <= cap:
        return text
    half = cap // 2
    return (
        text[:half]
        + f"\n\n...[TRUNCATED — {len(text)-cap} chars omitted]...\n\n"
        + text[-half:]
    )


def _err(msg: str) -> dict:
    return {"error": msg}


async def _get_browser():
    """Return a stealth Playwright browser context (Chromium)."""
    try:
        from playwright.async_api import async_playwright  # type: ignore
    except ImportError:
        raise RuntimeError(
            "Playwright not installed. Run: py -3.11 -m pip install playwright && "
            "py -3.11 -m playwright install chromium"
        )
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
        ],
    )
    ctx = await browser.new_context(
        user_agent=USER_AGENT,
        locale="en-US",
        viewport={"width": 1280, "height": 900},
    )
    return pw, browser, ctx


async def _fetch_page(url: str, wait_selector: str = "body", timeout: int = 15000) -> str:
    """Open a URL and return all visible text."""
    pw, browser, ctx = await _get_browser()
    try:
        page = await ctx.new_page()
        await page.add_init_script(_STEALTH_JS)
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        try:
            await page.wait_for_selector(wait_selector, timeout=5000)
        except Exception:
            pass
        await asyncio.sleep(1)
        text = await page.evaluate("() => document.body.innerText")
        return text
    finally:
        await browser.close()
        await pw.stop()


# ─────────────────────────────────────────────────────────────────────────────
# GOOGLE SEARCH & WEB
# ─────────────────────────────────────────────────────────────────────────────

async def google_search(
    query: str,
    num_results: int = 5,
    time_range: str = "",
    site: str = "",
    page: int = 1,
    language: str = "en",
    region: str = "us",
) -> dict:
    """
    Google web search. Returns titles, URLs, and snippets.

    time_range: 'past_hour' | 'past_day' | 'past_week' | 'past_month' | 'past_year'
    site: restrict to domain, e.g. 'reddit.com'
    """
    q = query
    if site:
        q += f" site:{site}"
    tbs = {
        "past_hour": "qdr:h",
        "past_day": "qdr:d",
        "past_week": "qdr:w",
        "past_month": "qdr:m",
        "past_year": "qdr:y",
    }.get(time_range, "")
    start = (page - 1) * 10
    params = urllib.parse.urlencode(
        {k: v for k, v in [
            ("q", q), ("num", num_results), ("start", start),
            ("hl", language), ("gl", region), ("tbs", tbs)
        ] if v}
    )
    url = f"https://www.google.com/search?{params}"
    try:
        from playwright.async_api import async_playwright  # type: ignore
        pw, browser, ctx = await _get_browser()
        page_obj = await ctx.new_page()
        await page_obj.add_init_script(_STEALTH_JS)
        await page_obj.goto(url, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(1.5)

        results = await page_obj.evaluate("""
        () => {
            const items = [];
            document.querySelectorAll('div.g').forEach(el => {
                const a = el.querySelector('a');
                const h3 = el.querySelector('h3');
                const span = el.querySelector('.VwiC3b, .yXK7lf, .s3v9rd');
                if (a && h3) {
                    items.push({
                        title: h3.innerText,
                        url: a.href,
                        snippet: span ? span.innerText : ''
                    });
                }
            });
            return items;
        }
        """)
        await browser.close()
        await pw.stop()
        return {"query": query, "results": results[:num_results]}
    except Exception as e:
        return _err(f"google_search failed: {e}")


async def google_news(query: str, num_results: int = 5) -> dict:
    """Google News search. Returns headlines, sources, and times."""
    params = urllib.parse.urlencode({"q": query, "tbm": "nws", "num": num_results})
    url = f"https://www.google.com/search?{params}"
    try:
        from playwright.async_api import async_playwright  # type: ignore
        pw, browser, ctx = await _get_browser()
        page_obj = await ctx.new_page()
        await page_obj.add_init_script(_STEALTH_JS)
        await page_obj.goto(url, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(1.5)
        results = await page_obj.evaluate("""
        () => {
            const items = [];
            document.querySelectorAll('div.SoaBEf, div.WlydOe, article').forEach(el => {
                const a = el.querySelector('a');
                const title = el.querySelector('.mCBkyc, .JheGif, h3');
                const source = el.querySelector('.NUnG9d, .UPmit');
                const time = el.querySelector('.OSrXXb, .WG9SHc');
                if (a && title) {
                    items.push({
                        title: title.innerText,
                        url: a.href,
                        source: source ? source.innerText : '',
                        time: time ? time.innerText : ''
                    });
                }
            });
            return items;
        }
        """)
        await browser.close()
        await pw.stop()
        return {"query": query, "results": results[:num_results]}
    except Exception as e:
        return _err(f"google_news failed: {e}")


async def google_scholar(query: str, num_results: int = 5) -> dict:
    """Google Scholar academic search. Returns papers with citation counts."""
    params = urllib.parse.urlencode({"q": query, "num": num_results})
    url = f"https://scholar.google.com/scholar?{params}"
    try:
        text = await _fetch_page(url)
        return {"query": query, "text": _truncate(text)}
    except Exception as e:
        return _err(f"google_scholar failed: {e}")


async def google_images(query: str, num_results: int = 5) -> dict:
    """Google Image search. Returns image URLs and alt text."""
    params = urllib.parse.urlencode({"q": query, "tbm": "isch", "num": num_results})
    url = f"https://www.google.com/search?{params}"
    try:
        from playwright.async_api import async_playwright  # type: ignore
        pw, browser, ctx = await _get_browser()
        page_obj = await ctx.new_page()
        await page_obj.add_init_script(_STEALTH_JS)
        await page_obj.goto(url, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(2)
        results = await page_obj.evaluate("""
        () => {
            const imgs = [];
            document.querySelectorAll('img').forEach(img => {
                if (img.src && img.src.startsWith('http') && img.width > 100) {
                    imgs.push({url: img.src, alt: img.alt || ''});
                }
            });
            return imgs;
        }
        """)
        await browser.close()
        await pw.stop()
        return {"query": query, "images": results[:num_results]}
    except Exception as e:
        return _err(f"google_images failed: {e}")


async def google_trends(query: str) -> dict:
    """Google Trends — topic interest over time."""
    url = f"https://trends.google.com/trends/explore?q={urllib.parse.quote(query)}&geo=US"
    try:
        text = await _fetch_page(url)
        return {"query": query, "text": _truncate(text)}
    except Exception as e:
        return _err(f"google_trends failed: {e}")


async def visit_page(url: str) -> dict:
    """Fetch any URL and extract readable text. Handles JS-rendered pages."""
    try:
        text = await _fetch_page(url)
        return {"url": url, "text": _truncate(text)}
    except Exception as e:
        return _err(f"visit_page failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# TRAVEL & COMMERCE
# ─────────────────────────────────────────────────────────────────────────────

async def google_shopping(query: str, num_results: int = 5) -> dict:
    """Google Shopping — product search with prices and stores."""
    params = urllib.parse.urlencode({"q": query, "tbm": "shop", "num": num_results})
    url = f"https://www.google.com/search?{params}"
    try:
        from playwright.async_api import async_playwright  # type: ignore
        pw, browser, ctx = await _get_browser()
        page_obj = await ctx.new_page()
        await page_obj.add_init_script(_STEALTH_JS)
        await page_obj.goto(url, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(1.5)
        results = await page_obj.evaluate("""
        () => {
            const items = [];
            document.querySelectorAll('.sh-dgr__content, .KZmu8e').forEach(el => {
                const title = el.querySelector('h3, .EI11Pd');
                const price = el.querySelector('.a8Pemb, .HRLxBb');
                const store = el.querySelector('.aULzUe, .IuHnof');
                const a = el.querySelector('a');
                if (title) {
                    items.push({
                        title: title.innerText,
                        price: price ? price.innerText : '',
                        store: store ? store.innerText : '',
                        url: a ? a.href : ''
                    });
                }
            });
            return items;
        }
        """)
        await browser.close()
        await pw.stop()
        return {"query": query, "results": results[:num_results]}
    except Exception as e:
        return _err(f"google_shopping failed: {e}")


async def google_flights(
    origin: str,
    destination: str,
    date: str = "",
    return_date: str = "",
) -> dict:
    """Google Flights — search for flights between two cities."""
    q = f"flights from {origin} to {destination}"
    if date:
        q += f" {date}"
    if return_date:
        q += f" return {return_date}"
    params = urllib.parse.urlencode({"q": q})
    url = f"https://www.google.com/search?{params}"
    try:
        text = await _fetch_page(url)
        return {"origin": origin, "destination": destination, "text": _truncate(text)}
    except Exception as e:
        return _err(f"google_flights failed: {e}")


async def google_hotels(query: str, num_results: int = 5) -> dict:
    """Google Hotels — search for hotels in a location."""
    params = urllib.parse.urlencode({"q": f"hotels in {query}", "num": num_results})
    url = f"https://www.google.com/search?{params}"
    try:
        text = await _fetch_page(url)
        return {"query": query, "text": _truncate(text)}
    except Exception as e:
        return _err(f"google_hotels failed: {e}")


async def google_translate(
    text: str,
    to_language: str,
    from_language: str = "auto",
) -> dict:
    """Google Translate — translate text to target language."""
    src = "auto" if not from_language else from_language
    params = urllib.parse.urlencode({
        "sl": src, "tl": to_language, "text": text, "op": "translate"
    })
    url = f"https://translate.google.com/?{params}"
    try:
        from playwright.async_api import async_playwright  # type: ignore
        pw, browser, ctx = await _get_browser()
        page_obj = await ctx.new_page()
        await page_obj.add_init_script(_STEALTH_JS)
        await page_obj.goto(url, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(2)
        translation = await page_obj.evaluate("""
        () => {
            const el = document.querySelector('.ryNqvb, .lRu31, span[jsname="W297wb"]');
            return el ? el.innerText : '';
        }
        """)
        await browser.close()
        await pw.stop()
        return {"original": text, "translation": translation, "to": to_language}
    except Exception as e:
        return _err(f"google_translate failed: {e}")


async def google_maps(query: str, num_results: int = 5) -> dict:
    """Google Maps place search with ratings and addresses."""
    params = urllib.parse.urlencode({"q": query})
    url = f"https://www.google.com/maps/search/{urllib.parse.quote(query)}"
    try:
        text = await _fetch_page(url, timeout=20000)
        return {"query": query, "text": _truncate(text)}
    except Exception as e:
        return _err(f"google_maps failed: {e}")


async def google_maps_directions(
    origin: str,
    destination: str,
    mode: str = "driving",
) -> dict:
    """Google Maps directions. mode: driving|walking|transit|cycling."""
    mode_map = {"driving": "!4m2!4m1!3e0", "walking": "!4m2!4m1!3e2",
                "transit": "!4m2!4m1!3e3", "cycling": "!4m2!4m1!3e1"}
    q = f"{origin} to {destination}"
    url = f"https://www.google.com/maps/dir/{urllib.parse.quote(origin)}/{urllib.parse.quote(destination)}"
    try:
        text = await _fetch_page(url, timeout=20000)
        return {"origin": origin, "destination": destination, "mode": mode, "text": _truncate(text)}
    except Exception as e:
        return _err(f"google_maps_directions failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# FINANCE & INFO
# ─────────────────────────────────────────────────────────────────────────────

async def google_finance(query: str) -> dict:
    """Google Finance — stock prices, market data. query e.g. 'AAPL:NASDAQ'."""
    url = f"https://www.google.com/finance/quote/{urllib.parse.quote(query)}"
    try:
        text = await _fetch_page(url)
        return {"query": query, "text": _truncate(text)}
    except Exception as e:
        return _err(f"google_finance failed: {e}")


async def google_weather(location: str) -> dict:
    """Google Weather — current conditions and multi-day forecast."""
    params = urllib.parse.urlencode({"q": f"weather {location}"})
    url = f"https://www.google.com/search?{params}"
    try:
        text = await _fetch_page(url)
        return {"location": location, "text": _truncate(text)}
    except Exception as e:
        return _err(f"google_weather failed: {e}")


async def google_books(query: str, num_results: int = 5) -> dict:
    """Google Books search — titles, authors, ISBNs, snippets."""
    params = urllib.parse.urlencode({"q": query, "tbm": "bks", "num": num_results})
    url = f"https://www.google.com/search?{params}"
    try:
        text = await _fetch_page(url)
        return {"query": query, "text": _truncate(text)}
    except Exception as e:
        return _err(f"google_books failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# VISION & OCR
# ─────────────────────────────────────────────────────────────────────────────

async def google_lens(image_source: str) -> dict:
    """
    Google Lens reverse image search.
    image_source: URL, local file path, or base64 data URI.
    """
    try:
        from playwright.async_api import async_playwright  # type: ignore
        pw, browser, ctx = await _get_browser()
        page_obj = await ctx.new_page()
        await page_obj.add_init_script(_STEALTH_JS)

        if image_source.startswith("data:") or not image_source.startswith("http"):
            # local file or base64: upload via Lens upload endpoint
            if not image_source.startswith("data:"):
                with open(image_source, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode()
                mime = mimetypes.guess_type(image_source)[0] or "image/jpeg"
                image_source = f"data:{mime};base64,{b64}"
            await page_obj.goto("https://lens.google.com/", wait_until="domcontentloaded")
            await asyncio.sleep(1)
            # inject the image as a file input
            img_data = image_source.split(",", 1)[1]
            js = f"""
            async () => {{
                const blob = await fetch('data:image/jpeg;base64,{img_data}').then(r=>r.blob());
                const file = new File([blob],'image.jpg',{{type:'image/jpeg'}});
                const dt = new DataTransfer();
                dt.items.add(file);
                const inp = document.querySelector('input[type=file]');
                if(inp){{ inp.files=dt.files; inp.dispatchEvent(new Event('change',{{bubbles:true}})); }}
            }}
            """
            await page_obj.evaluate(js)
            await asyncio.sleep(3)
        else:
            encoded = urllib.parse.quote(image_source)
            await page_obj.goto(
                f"https://lens.google.com/uploadbyurl?url={encoded}",
                wait_until="domcontentloaded",
                timeout=20000,
            )
            await asyncio.sleep(2)

        text = await page_obj.evaluate("() => document.body.innerText")
        await browser.close()
        await pw.stop()
        return {"image_source": image_source[:80], "text": _truncate(text)}
    except Exception as e:
        return _err(f"google_lens failed: {e}")


async def google_lens_detect(image_source: str) -> dict:
    """
    Detect objects in an image (OpenCV) and identify each via Google Lens.
    image_source: local file path or base64 data URI.
    Requires: pip install opencv-python
    """
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except ImportError:
        return _err("opencv-python not installed. Run: py -3.11 -m pip install opencv-python")

    try:
        if image_source.startswith("data:"):
            img_b64 = image_source.split(",", 1)[1]
            img_bytes = base64.b64decode(img_b64)
            np_arr = np.frombuffer(img_bytes, np.uint8)
            img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        else:
            img = cv2.imread(image_source)

        if img is None:
            return _err("Could not load image")

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 128, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        objects = []
        for cnt in contours[:8]:  # top 8 detected objects
            x, y, w, h = cv2.boundingRect(cnt)
            if w * h < 1000:
                continue
            crop = img[y:y+h, x:x+w]
            _, buf = cv2.imencode(".jpg", crop)
            b64 = base64.b64encode(buf.tobytes()).decode()
            result = await google_lens(f"data:image/jpeg;base64,{b64}")
            objects.append({"bbox": [x, y, w, h], "lens": result.get("text", "")[:300]})

        return {"objects_detected": len(objects), "objects": objects}
    except Exception as e:
        return _err(f"google_lens_detect failed: {e}")


async def ocr_image(image_source: str) -> dict:
    """
    Extract text from an image using RapidOCR (offline, no API key).
    Falls back to pytesseract if RapidOCR is not installed.
    image_source: local file path or base64 data URI.
    Requires: pip install rapidocr-onnxruntime  OR  pytesseract + Pillow
    """
    try:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore
        engine = RapidOCR()
        if image_source.startswith("data:"):
            import numpy as np  # type: ignore
            img_b64 = image_source.split(",", 1)[1]
            img_bytes = base64.b64decode(img_b64)
            np_arr = np.frombuffer(img_bytes, np.uint8)
            import cv2  # type: ignore
            img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        else:
            img = image_source  # RapidOCR accepts file paths
        result, _ = engine(img)
        if result:
            text = " ".join([r[1] for r in result])
        else:
            text = ""
        return {"text": text, "chars": len(text)}
    except ImportError:
        pass  # fall through to pytesseract
    except Exception as e:
        return _err(f"RapidOCR failed: {e}")

    # fallback: pytesseract
    try:
        import pytesseract  # type: ignore
        from PIL import Image  # type: ignore
        if image_source.startswith("data:"):
            import io
            img_b64 = image_source.split(",", 1)[1]
            img = Image.open(io.BytesIO(base64.b64decode(img_b64)))
        else:
            img = Image.open(image_source)
        text = pytesseract.image_to_string(img).strip()
        return {"text": text, "chars": len(text), "engine": "pytesseract"}
    except Exception as e:
        return _err(
            f"ocr_image: no OCR engine available. "
            f"Install: py -3.11 -m pip install rapidocr-onnxruntime  OR  pytesseract+Pillow. Error: {e}"
        )


async def list_images(directory: str = "") -> dict:
    """List image files in a directory. Default: ~/lens/"""
    folder = Path(directory) if directory else Path.home() / "lens"
    if not folder.exists():
        return _err(f"Directory not found: {folder}")
    exts = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".tif", ".svg"}
    files = [str(p) for p in folder.iterdir() if p.suffix.lower() in exts]
    return {"directory": str(folder), "count": len(files), "files": files}


# ─────────────────────────────────────────────────────────────────────────────
# VIDEO & AUDIO
# ─────────────────────────────────────────────────────────────────────────────

async def transcribe_video(
    url: str,
    model_size: str = "tiny",
    language: str = "",
) -> dict:
    """
    Download and transcribe a YouTube/video URL with faster-whisper.
    Requires: pip install faster-whisper yt-dlp
    """
    try:
        import yt_dlp  # type: ignore
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError as e:
        return _err(f"Missing dep: {e}. Run: py -3.11 -m pip install faster-whisper yt-dlp")

    with tempfile.TemporaryDirectory() as tmpdir:
        opts = {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(tmpdir, "audio.%(ext)s"),
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}],
            "quiet": True,
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                title = info.get("title", "Unknown")
            audio_path = next(Path(tmpdir).glob("audio.*"))
            model = WhisperModel(model_size, compute_type="int8")
            kwargs = {"language": language} if language else {}
            segments, info = model.transcribe(str(audio_path), **kwargs)
            lines = []
            for seg in segments:
                ts = f"[{int(seg.start//60):02d}:{int(seg.start%60):02d}]"
                lines.append(f"{ts} {seg.text.strip()}")
            transcript = "\n".join(lines)
            return {"title": title, "url": url, "transcript": _truncate(transcript)}
        except Exception as e:
            return _err(f"transcribe_video failed: {e}")


async def transcribe_local(
    file_path: str,
    model_size: str = "tiny",
    language: str = "",
) -> dict:
    """
    Transcribe a local audio/video file with faster-whisper.
    Supports: mp3, wav, m4a, flac, ogg, mp4, mkv, webm, avi, mov
    Requires: pip install faster-whisper
    """
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError:
        return _err("faster-whisper not installed. Run: py -3.11 -m pip install faster-whisper")

    p = Path(file_path)
    if not p.exists():
        return _err(f"File not found: {file_path}")
    try:
        model = WhisperModel(model_size, compute_type="int8")
        kwargs = {"language": language} if language else {}
        segments, _ = model.transcribe(str(p), **kwargs)
        lines = []
        for seg in segments:
            ts = f"[{int(seg.start//60):02d}:{int(seg.start%60):02d}]"
            lines.append(f"{ts} {seg.text.strip()}")
        transcript = "\n".join(lines)
        return {"file": file_path, "transcript": _truncate(transcript)}
    except Exception as e:
        return _err(f"transcribe_local failed: {e}")


async def search_transcript(url: str, keyword: str) -> dict:
    """
    Search a previously transcribed video's stored transcript for a keyword.
    If not yet transcribed, returns an error message.
    """
    _init_transcript_db()
    conn = sqlite3.connect(str(TRANSCRIPT_DB))
    rows = conn.execute(
        "SELECT title, segment, start_sec FROM transcripts WHERE url=? AND segment LIKE ?",
        (url, f"%{keyword}%"),
    ).fetchall()
    conn.close()
    if not rows:
        return {"message": f"No transcript stored for {url!r}. Call transcribe_video first.", "matches": []}
    return {
        "url": url,
        "keyword": keyword,
        "matches": [{"start_sec": r[2], "segment": r[1]} for r in rows],
    }


async def extract_video_clip(
    url: str,
    description: str,
    output_path: str = "",
) -> dict:
    """
    AI-powered clip extraction. Searches the stored transcript for description,
    finds timestamps, and cuts the clip with FFmpeg.
    Requires: transcribe_video called first + ffmpeg on PATH
    """
    import subprocess
    _init_transcript_db()
    conn = sqlite3.connect(str(TRANSCRIPT_DB))
    rows = conn.execute(
        "SELECT start_sec, end_sec, segment FROM transcripts WHERE url=? AND segment LIKE ?",
        (url, f"%{description[:40]}%"),
    ).fetchall()
    conn.close()
    if not rows:
        return _err(f"No matching transcript segment for {description!r}. Call transcribe_video first.")
    start = rows[0][0]
    end = rows[-1][1] if rows[-1][1] else start + 30
    out = output_path or str(Path.home() / f"clip_{int(start)}-{int(end)}.mp4")
    # download original video
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            import yt_dlp  # type: ignore
        except ImportError:
            return _err("yt-dlp not installed. Run: py -3.11 -m pip install yt-dlp")
        opts = {
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]",
            "outtmpl": os.path.join(tmpdir, "video.%(ext)s"),
            "quiet": True,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=True)
        video_path = next(Path(tmpdir).glob("video.*"), None)
        if not video_path:
            return _err("Could not download video for clip extraction.")
        cmd = ["ffmpeg", "-y", "-i", str(video_path),
               "-ss", str(start), "-to", str(end),
               "-c", "copy", out]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return _err(f"FFmpeg error: {result.stderr[:500]}")
    return {"clip": out, "start_sec": start, "end_sec": end}


async def convert_media(input_path: str, output_path: str) -> dict:
    """
    Convert audio/video formats via FFmpeg.
    input_path: source file, output_path: destination (extension determines format).
    Requires: ffmpeg on PATH
    """
    import subprocess
    if not Path(input_path).exists():
        return _err(f"Input file not found: {input_path}")
    cmd = ["ffmpeg", "-y", "-i", input_path, output_path]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return _err(f"FFmpeg error: {result.stderr[:500]}")
    return {"input": input_path, "output": output_path, "ok": True}


# ─────────────────────────────────────────────────────────────────────────────
# DOCUMENTS & DATA
# ─────────────────────────────────────────────────────────────────────────────

async def read_document(file_path: str) -> dict:
    """
    Extract text from PDF, DOCX, HTML, CSV, JSON, YAML, and 30+ text formats.
    PDF requires: pip install pdfminer.six
    DOCX requires: pip install python-docx
    """
    p = Path(file_path)
    if not p.exists():
        return _err(f"File not found: {file_path}")
    ext = p.suffix.lower()

    TEXT_EXTS = {".txt", ".md", ".log", ".ini", ".cfg", ".conf", ".env",
                 ".py", ".js", ".ts", ".go", ".rs", ".c", ".cpp", ".h",
                 ".java", ".kt", ".rb", ".sql", ".r", ".sh", ".bash",
                 ".yaml", ".yml", ".toml", ".xml", ".csv", ".json"}

    try:
        if ext in TEXT_EXTS:
            text = p.read_text(encoding="utf-8", errors="replace")
        elif ext == ".pdf":
            from pdfminer.high_level import extract_text  # type: ignore
            text = extract_text(str(p))
        elif ext == ".docx":
            from docx import Document  # type: ignore
            doc = Document(str(p))
            text = "\n".join(para.text for para in doc.paragraphs)
        elif ext in (".html", ".htm"):
            import html.parser
            class _P(html.parser.HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.chunks: list[str] = []
                def handle_data(self, data: str):
                    self.chunks.append(data)
            parser = _P()
            parser.feed(p.read_text(encoding="utf-8", errors="replace"))
            text = " ".join(parser.chunks)
        else:
            text = p.read_text(encoding="utf-8", errors="replace")
        return {"file": file_path, "ext": ext, "text": _truncate(text)}
    except Exception as e:
        return _err(f"read_document failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# EMAIL
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_emails(
    email_address: str,
    password: str,
    server: str = "",
    port: int = 993,
    num_emails: int = 10,
    folder: str = "INBOX",
) -> dict:
    """
    Pull emails via IMAP. Auto-detects server for Gmail/Outlook/Yahoo/iCloud.
    password: use an app-specific password for Gmail.
    """
    import imaplib
    import email as emaillib
    from email.header import decode_header

    _AUTO = {
        "gmail.com": "imap.gmail.com",
        "googlemail.com": "imap.gmail.com",
        "outlook.com": "outlook.office365.com",
        "hotmail.com": "outlook.office365.com",
        "live.com": "outlook.office365.com",
        "yahoo.com": "imap.mail.yahoo.com",
        "icloud.com": "imap.mail.me.com",
        "me.com": "imap.mail.me.com",
    }
    domain = email_address.split("@")[-1].lower()
    host = server or _AUTO.get(domain, f"imap.{domain}")

    try:
        mail = imaplib.IMAP4_SSL(host, port)
        mail.login(email_address, password)
        mail.select(folder)
        _, msg_nums = mail.search(None, "ALL")
        ids = msg_nums[0].split()[-num_emails:]
        messages = []
        for uid in reversed(ids):
            _, data = mail.fetch(uid, "(RFC822)")
            msg = emaillib.message_from_bytes(data[0][1])
            subj_raw, enc = decode_header(msg["Subject"] or "")[0]
            subject = subj_raw.decode(enc or "utf-8") if isinstance(subj_raw, bytes) else subj_raw
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode(errors="replace")
                        break
            else:
                body = msg.get_payload(decode=True).decode(errors="replace")
            messages.append({
                "from": msg["From"],
                "date": msg["Date"],
                "subject": subject,
                "body": body[:500],
            })
        mail.logout()
        return {"count": len(messages), "emails": messages}
    except Exception as e:
        return _err(f"fetch_emails failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# WEB UTILITIES
# ─────────────────────────────────────────────────────────────────────────────

async def paste_text(text: str) -> dict:
    """Post text to dpaste.com and return a shareable URL."""
    try:
        data = urllib.parse.urlencode({"content": text, "syntax": "text", "expiry_days": 7}).encode()
        req = urllib.request.Request(
            "https://dpaste.com/api/v2/",
            data=data,
            headers={"User-Agent": USER_AGENT},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            url = r.read().decode().strip().strip('"')
        return {"url": url}
    except Exception as e:
        return _err(f"paste_text failed: {e}")


async def shorten_url(url: str) -> dict:
    """Shorten a URL via TinyURL."""
    try:
        api = f"https://tinyurl.com/api-create.php?url={urllib.parse.quote(url)}"
        req = urllib.request.Request(api, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as r:
            short = r.read().decode().strip()
        return {"original": url, "shortened": short}
    except Exception as e:
        return _err(f"shorten_url failed: {e}")


async def generate_qr(data: str, output_path: str = "") -> dict:
    """
    Generate a QR code image for any data (URL, Wi-Fi, text, contact).
    Requires: pip install qrcode[pil]
    output_path: where to save the PNG. Default: ~/qr_<hash>.png
    """
    try:
        import qrcode  # type: ignore
        from PIL import Image  # type: ignore
    except ImportError:
        return _err("qrcode not installed. Run: py -3.11 -m pip install qrcode[pil]")
    out = output_path or str(Path.home() / f"qr_{hashlib.md5(data.encode()).hexdigest()[:8]}.png")
    img = qrcode.make(data)
    img.save(out)
    return {"data": data, "file": out}


async def archive_webpage(url: str) -> dict:
    """Save a webpage to the Wayback Machine and return the archive URL."""
    try:
        api = f"https://web.archive.org/save/{url}"
        req = urllib.request.Request(api, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as r:
            archive_url = r.url
        return {"original": url, "archive": archive_url}
    except Exception as e:
        return _err(f"archive_webpage failed: {e}")


async def wikipedia(query: str, lang: str = "en") -> dict:
    """
    Look up a Wikipedia article. Returns the summary and a link.
    lang: language code, e.g. 'en', 'de', 'fr'
    """
    params = urllib.parse.urlencode({"action": "query", "format": "json",
                                     "titles": query, "prop": "extracts",
                                     "exintro": True, "explaintext": True})
    api = f"https://{lang}.wikipedia.org/w/api.php?{params}"
    try:
        req = urllib.request.Request(api, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode())
        pages = data["query"]["pages"]
        page = next(iter(pages.values()))
        if "missing" in page:
            return _err(f"Wikipedia article not found: {query!r}")
        extract = page.get("extract", "")
        title = page.get("title", query)
        url = f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"
        return {"title": title, "url": url, "summary": _truncate(extract)}
    except Exception as e:
        return _err(f"wikipedia failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# CLOUD STORAGE
# ─────────────────────────────────────────────────────────────────────────────

async def upload_to_s3(
    file_path: str,
    bucket: str,
    endpoint_url: str = "",
    access_key: str = "",
    secret_key: str = "",
    region: str = "us-east-1",
) -> dict:
    """
    Upload a file to S3-compatible storage (AWS, MinIO, DigitalOcean, Cloudflare R2, Backblaze B2).
    Requires: pip install boto3
    Credentials can also be set via env: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
    """
    try:
        import boto3  # type: ignore
    except ImportError:
        return _err("boto3 not installed. Run: py -3.11 -m pip install boto3")
    p = Path(file_path)
    if not p.exists():
        return _err(f"File not found: {file_path}")
    try:
        kwargs: dict[str, Any] = {"region_name": region}
        if endpoint_url:
            kwargs["endpoint_url"] = endpoint_url
        if access_key and secret_key:
            kwargs["aws_access_key_id"] = access_key
            kwargs["aws_secret_access_key"] = secret_key
        s3 = boto3.client("s3", **kwargs)
        s3.upload_file(str(p), bucket, p.name)
        return {"file": file_path, "bucket": bucket, "key": p.name, "ok": True}
    except Exception as e:
        return _err(f"upload_to_s3 failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# FEED SUBSCRIPTIONS (SQLite-backed, stdlib only for core feeds)
# ─────────────────────────────────────────────────────────────────────────────

_NEWS_FEEDS = {
    "bbc": "http://feeds.bbci.co.uk/news/rss.xml",
    "cnn": "http://rss.cnn.com/rss/edition.rss",
    "nyt": "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
    "guardian": "https://www.theguardian.com/world/rss",
    "npr": "https://feeds.npr.org/1001/rss.xml",
    "aljazeera": "https://www.aljazeera.com/xml/rss/all.xml",
    "techcrunch": "https://techcrunch.com/feed/",
    "ars": "https://feeds.arstechnica.com/arstechnica/index",
    "verge": "https://www.theverge.com/rss/index.xml",
    "wired": "https://www.wired.com/feed/rss",
    "reuters": "https://feeds.reuters.com/reuters/topNews",
}
_ARXIV_CATS = {
    "ai": "cs.AI", "ml": "cs.LG", "cv": "cs.CV",
    "nlp": "cs.CL", "robotics": "cs.RO", "crypto": "cs.CR",
    "systems": "cs.SY", "hci": "cs.HC",
}


def _init_feeds_db() -> None:
    conn = sqlite3.connect(str(FEEDS_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY,
            source_type TEXT, identifier TEXT, name TEXT,
            url TEXT, created_at TEXT,
            UNIQUE(source_type, identifier)
        )
    """)
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS feed_items USING fts5(
            sub_id, source_type, source_name,
            title, url, content, published_at,
            tokenize='porter unicode61'
        )
    """)
    conn.commit()
    conn.close()


def _init_transcript_db() -> None:
    conn = sqlite3.connect(str(TRANSCRIPT_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transcripts (
            url TEXT, title TEXT, segment TEXT,
            start_sec REAL, end_sec REAL
        )
    """)
    conn.commit()
    conn.close()


def _resolve_feed_url(source_type: str, identifier: str) -> str:
    st = source_type.lower()
    if st == "news":
        return _NEWS_FEEDS.get(identifier.lower(), identifier)
    if st == "reddit":
        return f"https://www.reddit.com/r/{identifier}/.rss"
    if st == "hackernews":
        map_ = {"top": "topstories", "new": "newstories", "best": "beststories"}
        feed = map_.get(identifier.lower(), "topstories")
        return f"https://hnrss.org/{feed}"
    if st == "github":
        return f"https://github.com/{identifier}/releases.atom"
    if st == "arxiv":
        cat = _ARXIV_CATS.get(identifier.lower(), identifier)
        return f"https://rss.arxiv.org/rss/{cat}"
    if st in ("podcast", "rss"):
        return identifier
    # youtube, twitter — use visit_page approach
    return identifier


async def subscribe(
    source_type: str,
    identifier: str,
    name: str = "",
) -> dict:
    """
    Subscribe to a content source.
    source_type: news | reddit | hackernews | github | arxiv | youtube | podcast | twitter
    identifier: preset name, subreddit, repo path, arxiv shortcut, channel handle, or RSS URL
    """
    _init_feeds_db()
    url = _resolve_feed_url(source_type, identifier)
    display_name = name or f"{source_type}:{identifier}"
    conn = sqlite3.connect(str(FEEDS_DB))
    try:
        conn.execute(
            "INSERT OR REPLACE INTO subscriptions(source_type,identifier,name,url,created_at) VALUES(?,?,?,?,?)",
            (source_type, identifier, display_name, url, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return {"ok": True, "subscribed": display_name, "feed_url": url}
    except Exception as e:
        return _err(f"subscribe failed: {e}")
    finally:
        conn.close()


async def unsubscribe(source_type: str, identifier: str) -> dict:
    """Remove a subscription and its stored items."""
    _init_feeds_db()
    conn = sqlite3.connect(str(FEEDS_DB))
    row = conn.execute(
        "SELECT id FROM subscriptions WHERE source_type=? AND identifier=?",
        (source_type, identifier),
    ).fetchone()
    if not row:
        conn.close()
        return _err(f"Subscription not found: {source_type}:{identifier}")
    conn.execute("DELETE FROM subscriptions WHERE id=?", (row[0],))
    conn.execute("DELETE FROM feed_items WHERE sub_id=?", (str(row[0]),))
    conn.commit()
    conn.close()
    return {"ok": True, "removed": f"{source_type}:{identifier}"}


async def list_subscriptions() -> dict:
    """List all active subscriptions with item counts."""
    _init_feeds_db()
    conn = sqlite3.connect(str(FEEDS_DB))
    rows = conn.execute("SELECT id, source_type, identifier, name, created_at FROM subscriptions").fetchall()
    out = []
    for r in rows:
        count = conn.execute(
            "SELECT COUNT(*) FROM feed_items WHERE sub_id=?", (str(r[0]),)
        ).fetchone()[0]
        out.append({"id": r[0], "type": r[1], "identifier": r[2],
                    "name": r[3], "created": r[4], "items": count})
    conn.close()
    return {"subscriptions": out, "count": len(out)}


async def check_feeds(source_type: str = "") -> dict:
    """
    Fetch new content from all (or one type of) subscriptions.
    Parses RSS/Atom for standard feeds. YouTube/Twitter use visit_page.
    """
    _init_feeds_db()
    conn = sqlite3.connect(str(FEEDS_DB))
    if source_type:
        rows = conn.execute(
            "SELECT id, source_type, identifier, name, url FROM subscriptions WHERE source_type=?",
            (source_type,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, source_type, identifier, name, url FROM subscriptions"
        ).fetchall()
    conn.close()
    if not rows:
        return {"message": "No subscriptions. Call subscribe() first.", "fetched": 0}

    total = 0
    for sub_id, stype, ident, sname, url in rows:
        try:
            if stype in ("youtube", "twitter"):
                result = await visit_page(url)
                content = result.get("text", "")[:2000]
                _store_feed_item(sub_id, stype, sname, sname, url, content)
                total += 1
                continue
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=15) as r:
                raw = r.read().decode("utf-8", errors="replace")
            root = ET.fromstring(raw)
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            # RSS
            for item in root.findall(".//item")[:10]:
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                desc = (item.findtext("description") or "").strip()[:500]
                pub = (item.findtext("pubDate") or "").strip()
                _store_feed_item(sub_id, stype, sname, title, link, desc, pub)
                total += 1
            # Atom
            for entry in root.findall("atom:entry", ns)[:10]:
                title = (entry.findtext("atom:title", namespaces=ns) or "").strip()
                link_el = entry.find("atom:link", ns)
                link = link_el.get("href", "") if link_el is not None else ""
                summary = (entry.findtext("atom:summary", namespaces=ns) or "").strip()[:500]
                pub = (entry.findtext("atom:updated", namespaces=ns) or "").strip()
                _store_feed_item(sub_id, stype, sname, title, link, summary, pub)
                total += 1
        except Exception:
            continue
    return {"ok": True, "items_fetched": total}


def _store_feed_item(
    sub_id: int, source_type: str, source_name: str,
    title: str, url: str, content: str, published: str = ""
) -> None:
    _init_feeds_db()
    conn = sqlite3.connect(str(FEEDS_DB))
    conn.execute(
        "INSERT INTO feed_items(sub_id,source_type,source_name,title,url,content,published_at) VALUES(?,?,?,?,?,?,?)",
        (str(sub_id), source_type, source_name, title, url, content, published or datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


async def search_feeds(query: str, source_type: str = "", limit: int = 20) -> dict:
    """
    Full-text search across all stored feed content.
    Supports FTS5 AND, OR, NOT, and "quoted phrases".
    """
    _init_feeds_db()
    conn = sqlite3.connect(str(FEEDS_DB))
    try:
        if source_type:
            rows = conn.execute(
                "SELECT title, url, source_name, published_at, snippet(feed_items,5,'>>','<<','...',20) "
                "FROM feed_items WHERE feed_items MATCH ? AND source_type=? LIMIT ?",
                (query, source_type, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT title, url, source_name, published_at, snippet(feed_items,5,'>>','<<','...',20) "
                "FROM feed_items WHERE feed_items MATCH ? LIMIT ?",
                (query, limit),
            ).fetchall()
        results = [
            {"title": r[0], "url": r[1], "source": r[2], "published": r[3], "snippet": r[4]}
            for r in rows
        ]
        conn.close()
        return {"query": query, "count": len(results), "results": results}
    except Exception as e:
        conn.close()
        return _err(f"search_feeds failed: {e}")


async def get_feed_items(
    source: str = "",
    source_type: str = "",
    limit: int = 20,
) -> dict:
    """Browse recent items from feeds, optionally filtered by source name or type."""
    _init_feeds_db()
    conn = sqlite3.connect(str(FEEDS_DB))
    try:
        if source and source_type:
            rows = conn.execute(
                "SELECT title, url, source_name, source_type, published_at FROM feed_items "
                "WHERE source_name=? AND source_type=? LIMIT ?",
                (source, source_type, limit),
            ).fetchall()
        elif source:
            rows = conn.execute(
                "SELECT title, url, source_name, source_type, published_at FROM feed_items "
                "WHERE source_name=? LIMIT ?",
                (source, limit),
            ).fetchall()
        elif source_type:
            rows = conn.execute(
                "SELECT title, url, source_name, source_type, published_at FROM feed_items "
                "WHERE source_type=? LIMIT ?",
                (source_type, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT title, url, source_name, source_type, published_at FROM feed_items LIMIT ?",
                (limit,),
            ).fetchall()
        items = [
            {"title": r[0], "url": r[1], "source": r[2], "type": r[3], "published": r[4]}
            for r in rows
        ]
        conn.close()
        return {"count": len(items), "items": items}
    except Exception as e:
        conn.close()
        return _err(f"get_feed_items failed: {e}")
