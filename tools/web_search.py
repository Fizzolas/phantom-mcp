"""
tools/web_search.py  —  Internet search & web tools for Phantom MCP

All 38 tools from noapi-google-search-mcp (VincentKaufmann) implemented
directly inside the Phantom tool layer using Playwright + headless Chromium.
No separate MCP server, no API keys, no usage limits.

Tool categories:
  Google Search & Web      : google_search, google_news, google_scholar,
                             google_images, google_trends, visit_page
  Travel & Commerce        : google_shopping, google_flights, google_hotels,
                             google_translate, google_maps, google_maps_directions
  Finance & Info           : google_finance, google_weather, google_books
  Vision & OCR             : google_lens, google_lens_detect, ocr_image, list_images
  Video & Audio            : transcribe_video, transcribe_local, search_transcript,
                             extract_video_clip, convert_media
  Documents & Data         : read_document
  Email                    : fetch_emails
  Web Utilities            : paste_text, shorten_url, generate_qr, archive_webpage,
                             wikipedia
  Cloud Storage            : upload_to_s3
  Feed Subscriptions       : subscribe, unsubscribe, list_subscriptions,
                             check_feeds, search_feeds, get_feed_items

Design notes:
  - All Google scraping goes through a shared _GoogleBrowser singleton that
    applies anti-bot stealth patches (webdriver hide, fake plugins, cookie
    persistence, human-like delays) matching noapi-google-search-mcp v0.3.1.
  - The server.py internet routing logic decides WHEN to call these tools;
    this file only implements HOW they work.
  - Heavy optional deps (faster-whisper, yt-dlp, rapidocr, pdfminer, docx2txt,
    ffmpeg-python, boto3) are imported lazily so the server starts even if
    they are not installed. Each tool returns a clear MISSING_DEP error.

Install all optional deps at once:
  py -3.11 -m pip install playwright faster-whisper yt-dlp rapidocr-openinfer \
      pdfminer.six docx2txt feedparser boto3 qrcode Pillow
  py -3.11 -m playwright install chromium
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import random
import re
import sqlite3
import tempfile
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

# ─── project root ────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

# ─── missing-dep helper ──────────────────────────────────────────────────────
def _missing(dep: str, install: str) -> dict:
    return {"error": f"MISSING_DEP: {dep}", "install": install}


# =============================================================================
# SHARED PLAYWRIGHT BROWSER  (anti-bot stealth)
# =============================================================================
class _GoogleBrowser:
    """Singleton headless Chromium browser with anti-bot stealth patches."""

    _instance: "_GoogleBrowser | None" = None
    _lock = asyncio.Lock()

    def __init__(self):
        self._pw = None
        self._browser = None
        self._context = None
        self._cookie_path = DATA / "google_cookies.json"

    @classmethod
    async def get(cls) -> "_GoogleBrowser":
        async with cls._lock:
            if cls._instance is None or cls._instance._browser is None:
                inst = cls()
                await inst._start()
                cls._instance = inst
            return cls._instance

    async def _start(self):
        try:
            from playwright.async_api import async_playwright  # type: ignore
        except ImportError:
            raise ImportError("playwright not installed. Run: py -3.11 -m pip install playwright && py -3.11 -m playwright install chromium")

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--window-size=1280,800",
            ],
        )
        context_opts: dict = {
            "viewport": {"width": 1280, "height": 800},
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "locale": "en-US",
        }
        if self._cookie_path.exists():
            try:
                context_opts["storage_state"] = str(self._cookie_path)
            except Exception:
                pass
        self._context = await self._browser.new_context(**context_opts)
        await self._context.add_init_script("""
            Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
            window.chrome={runtime:{}};
            Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});
            Object.defineProperty(navigator,'languages',{get:()=>['en-US','en']});
        """)

    async def new_page(self):
        if self._context is None:
            await self._start()
        page = await self._context.new_page()
        return page

    async def save_cookies(self):
        if self._context:
            try:
                await self._context.storage_state(path=str(self._cookie_path))
            except Exception:
                pass

    async def close(self):
        try:
            if self._browser:
                await self._browser.close()
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass
        self._browser = None
        self._context = None


async def _google_page(url: str, wait_selector: str | None = None, timeout: int = 20_000) -> Any:
    """Open a URL in the stealth browser and return the page object."""
    gb = await _GoogleBrowser.get()
    page = await gb.new_page()
    await asyncio.sleep(random.uniform(0.3, 0.9))
    await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
    if wait_selector:
        try:
            await page.wait_for_selector(wait_selector, timeout=timeout)
        except Exception:
            pass
    await asyncio.sleep(random.uniform(0.2, 0.6))
    return page


def _q(text: str) -> str:
    return urllib.parse.quote_plus(text)


# =============================================================================
# 1. GOOGLE SEARCH & WEB
# =============================================================================
async def google_search(
    query: str,
    num_results: int = 5,
    time_range: str = "",
    site: str = "",
    page: int = 1,
    language: str = "en",
    region: str = "us",
) -> dict:
    q = query
    if site:
        q = f"site:{site} {q}"
    tbs = {"past_hour": "qdr:h", "past_day": "qdr:d", "past_week": "qdr:w",
           "past_month": "qdr:m", "past_year": "qdr:y"}.get(time_range, "")
    params = {"q": q, "num": str(min(num_results, 10)), "hl": language,
              "gl": region, "start": str((page - 1) * 10)}
    if tbs:
        params["tbs"] = tbs
    url = "https://www.google.com/search?" + urllib.parse.urlencode(params)
    pg = await _google_page(url, wait_selector="#search")
    results = await pg.evaluate("""
        () => {
            const items = [];
            document.querySelectorAll('div.g, div[data-sokoban-container]').forEach(el => {
                const a = el.querySelector('a[href]');
                const h = el.querySelector('h3');
                const span = el.querySelector('div[style*="-webkit-line-clamp"], .VwiC3b, span.st');
                if (a && h) items.push({
                    title: h.innerText,
                    url: a.href,
                    snippet: span ? span.innerText : ''
                });
            });
            return items.slice(0, 10);
        }
    """)
    await pg.close()
    gb = await _GoogleBrowser.get()
    await gb.save_cookies()
    return {"query": query, "results": results[:num_results]}


async def google_news(query: str, num_results: int = 5) -> dict:
    url = f"https://www.google.com/search?q={_q(query)}&tbm=nws&num={min(num_results,10)}"
    pg = await _google_page(url)
    results = await pg.evaluate("""
        () => {
            const items = [];
            document.querySelectorAll('article, div.SoaBEf, div.WlydOe').forEach(el => {
                const a = el.querySelector('a');
                const h = el.querySelector('div[role=heading], h3, .nDgy9d');
                const src = el.querySelector('.CEMjEf, .NUnG9d span');
                const t = el.querySelector('.OSrXXb, .ZE0LJd');
                if (a && h) items.push({
                    title: h.innerText,
                    url: a.href,
                    source: src ? src.innerText : '',
                    time: t ? t.innerText : ''
                });
            });
            return items;
        }
    """)
    await pg.close()
    return {"query": query, "articles": results[:num_results]}


async def google_scholar(query: str, num_results: int = 5) -> dict:
    url = f"https://scholar.google.com/scholar?q={_q(query)}&hl=en&num={min(num_results,10)}"
    pg = await _google_page(url, wait_selector=".gs_r")
    results = await pg.evaluate("""
        () => {
            const items = [];
            document.querySelectorAll('.gs_r.gs_or').forEach(el => {
                const h = el.querySelector('.gs_rt a, .gs_rt span');
                const a = el.querySelector('.gs_rt a');
                const auth = el.querySelector('.gs_a');
                const snip = el.querySelector('.gs_rs');
                const cite = el.querySelector('.gs_fl a');
                items.push({
                    title: h ? h.innerText : '',
                    url: a ? a.href : '',
                    authors: auth ? auth.innerText : '',
                    snippet: snip ? snip.innerText : '',
                    citations: cite ? cite.innerText : ''
                });
            });
            return items;
        }
    """)
    await pg.close()
    return {"query": query, "papers": results[:num_results]}


async def google_images(query: str, num_results: int = 5) -> dict:
    url = f"https://www.google.com/search?q={_q(query)}&tbm=isch"
    pg = await _google_page(url, wait_selector="img.rg_i,img.YQ4gaf")
    results = await pg.evaluate("""
        () => {
            const imgs = [];
            document.querySelectorAll('img.rg_i, img.YQ4gaf, .isv-r img').forEach(img => {
                const src = img.src || img.dataset.src || '';
                const alt = img.alt || '';
                if (src && src.startsWith('http')) imgs.push({url: src, alt});
            });
            return imgs;
        }
    """)
    await pg.close()
    return {"query": query, "images": results[:num_results]}


async def google_trends(query: str) -> dict:
    url = f"https://trends.google.com/trends/explore?q={_q(query)}&date=today%2012-m"
    pg = await _google_page(url)
    await asyncio.sleep(2)
    text = await pg.inner_text("body")
    await pg.close()
    return {"query": query, "trends_text": text[:3000]}


async def visit_page(url: str) -> dict:
    pg = await _google_page(url)
    await asyncio.sleep(1)
    text = await pg.evaluate("""
        () => {
            const clone = document.cloneNode(true);
            ['script','style','nav','footer','header','aside'].forEach(t => {
                clone.querySelectorAll(t).forEach(el => el.remove());
            });
            return (clone.body || clone).innerText;
        }
    """)
    title = await pg.title()
    final_url = pg.url
    await pg.close()
    return {"title": title, "url": final_url, "text": text[:12000]}


# =============================================================================
# 2. TRAVEL & COMMERCE
# =============================================================================
async def google_shopping(query: str, num_results: int = 5) -> dict:
    url = f"https://www.google.com/search?q={_q(query)}&tbm=shop"
    pg = await _google_page(url)
    results = await pg.evaluate("""
        () => {
            const items = [];
            document.querySelectorAll('.sh-dgr__grid-result, .KZmu8e').forEach(el => {
                const name = el.querySelector('h3,.Xjkr3b');
                const price = el.querySelector('.a8Pemb,.kHxwFf');
                const store = el.querySelector('.aULzUe,.E5ocAb');
                const a = el.querySelector('a');
                items.push({
                    name: name ? name.innerText : '',
                    price: price ? price.innerText : '',
                    store: store ? store.innerText : '',
                    url: a ? a.href : ''
                });
            });
            return items;
        }
    """)
    await pg.close()
    return {"query": query, "products": results[:num_results]}


async def google_flights(origin: str, destination: str, date: str = "", return_date: str = "") -> dict:
    q = f"flights from {origin} to {destination}"
    if date:
        q += f" {date}"
    if return_date:
        q += f" return {return_date}"
    url = f"https://www.google.com/search?q={_q(q)}"
    pg = await _google_page(url)
    await asyncio.sleep(2)
    text = await pg.inner_text("body")
    await pg.close()
    return {"origin": origin, "destination": destination, "date": date, "text": text[:4000]}


async def google_hotels(query: str, num_results: int = 5) -> dict:
    url = f"https://www.google.com/search?q=hotels+{_q(query)}"
    pg = await _google_page(url)
    await asyncio.sleep(2)
    results = await pg.evaluate("""
        () => {
            const items = [];
            document.querySelectorAll('[data-hveid] h3, .BTP4rb h3').forEach(h => {
                const parent = h.closest('[data-hveid]') || h.parentElement;
                const price = parent ? parent.querySelector('.priced-rating,.kR1ePe') : null;
                const rating = parent ? parent.querySelector('.KFi5wf,.yi40Hd') : null;
                items.push({
                    name: h.innerText,
                    price: price ? price.innerText : '',
                    rating: rating ? rating.innerText : ''
                });
            });
            return items;
        }
    """)
    await pg.close()
    return {"query": query, "hotels": results[:num_results]}


async def google_translate(text: str, to_language: str, from_language: str = "auto") -> dict:
    url = f"https://translate.google.com/?sl={_q(from_language)}&tl={_q(to_language)}&text={_q(text)}&op=translate"
    pg = await _google_page(url, wait_selector=".ryNqvb,.J0lOec")
    await asyncio.sleep(2)
    translated = await pg.evaluate("""
        () => {
            const el = document.querySelector('.ryNqvb, .J0lOec, span[jsname=W297wb]');
            return el ? el.innerText : '';
        }
    """)
    await pg.close()
    return {"original": text, "translated": translated, "to": to_language}


async def google_maps(query: str, num_results: int = 5) -> dict:
    url = f"https://www.google.com/maps/search/{_q(query)}"
    pg = await _google_page(url)
    await asyncio.sleep(3)
    results = await pg.evaluate("""
        () => {
            const items = [];
            document.querySelectorAll('.Nv2PK,.qjESne').forEach(el => {
                const name = el.querySelector('.qBF1Pd,.fontHeadlineSmall');
                const rating = el.querySelector('.MW4etd,.AJB7ye span');
                const addr = el.querySelector('.W4Efsd,.fontBodyMedium span');
                items.push({
                    name: name ? name.innerText : '',
                    rating: rating ? rating.innerText : '',
                    address: addr ? addr.innerText : ''
                });
            });
            return items;
        }
    """)
    await pg.close()
    return {"query": query, "places": results[:num_results]}


async def google_maps_directions(origin: str, destination: str, mode: str = "driving") -> dict:
    url = f"https://www.google.com/maps/dir/{_q(origin)}/{_q(destination)}/?travelmode={mode}"
    pg = await _google_page(url)
    await asyncio.sleep(3)
    steps = await pg.evaluate("""
        () => {
            const steps = [];
            document.querySelectorAll('.VuCHmb,.y0skZc').forEach(s => steps.push(s.innerText));
            return steps;
        }
    """)
    summary = await pg.evaluate("() => { const el=document.querySelector('.Fk3sm,.UdvAnc'); return el?el.innerText:''; }")
    await pg.close()
    return {"origin": origin, "destination": destination, "mode": mode, "summary": summary, "steps": steps[:20]}


# =============================================================================
# 3. FINANCE & INFO
# =============================================================================
async def google_finance(query: str) -> dict:
    url = f"https://www.google.com/finance/quote/{_q(query)}"
    pg = await _google_page(url)
    await asyncio.sleep(1)
    data = await pg.evaluate("""
        () => {
            const price = document.querySelector('[data-last-price],[jsname=T4uCfd],.IsqQVc');
            const change = document.querySelector('[data-last-percent-change],.JwB6zf,.NydbP');
            const name = document.querySelector('.zzDege');
            return {
                ticker: window.location.pathname.split('/').pop(),
                name: name ? name.innerText : '',
                price: price ? price.innerText : '',
                change: change ? change.innerText : ''
            };
        }
    """)
    await pg.close()
    return data


async def google_weather(location: str) -> dict:
    url = f"https://www.google.com/search?q=weather+{_q(location)}"
    pg = await _google_page(url, wait_selector="#wob_tm,.wob_t")
    data = await pg.evaluate("""
        () => {
            const temp = document.querySelector('#wob_tm,.wob_t');
            const desc = document.querySelector('#wob_dc,.wob_dc');
            const hum  = document.querySelector('#wob_hm,.wob_hm');
            const wind = document.querySelector('#wob_ws,.wob_ws');
            const loc  = document.querySelector('#wob_loc');
            const forecast = [];
            document.querySelectorAll('.wob_df').forEach(d => {
                const day  = d.querySelector('.wob_t,.ZkKBhe');
                const hi   = d.querySelector('.wob_t[id^=wob_t],.gNCp2e');
                const lo   = d.querySelector('[id^=wob_l],.QrNVmd');
                const cond = d.querySelector('img');
                if (day) forecast.push({
                    day:  day.innerText,
                    high: hi  ? hi.innerText  : '',
                    low:  lo  ? lo.innerText  : '',
                    cond: cond ? cond.alt : ''
                });
            });
            return {
                location: loc  ? loc.innerText  : '',
                temp:     temp ? temp.innerText : '',
                desc:     desc ? desc.innerText : '',
                humidity: hum  ? hum.innerText  : '',
                wind:     wind ? wind.innerText : '',
                forecast: forecast.slice(0,8)
            };
        }
    """)
    await pg.close()
    return data


async def google_books(query: str, num_results: int = 5) -> dict:
    url = f"https://www.google.com/search?q={_q(query)}&tbm=bks"
    pg = await _google_page(url)
    results = await pg.evaluate("""
        () => {
            const items = [];
            document.querySelectorAll('.Yr5TG,.bHexk').forEach(el => {
                const h = el.querySelector('h3 a, a.l');
                const auth = el.querySelector('.fl, span.f');
                const snip = el.querySelector('.book-description,.Uroaid,.st');
                items.push({
                    title: h ? h.innerText : '',
                    url: h ? h.href : '',
                    author: auth ? auth.innerText : '',
                    snippet: snip ? snip.innerText : ''
                });
            });
            return items;
        }
    """)
    await pg.close()
    return {"query": query, "books": results[:num_results]}


# =============================================================================
# 4. VISION & OCR
# =============================================================================
async def google_lens(image_source: str) -> dict:
    """Reverse image search via Google Lens. image_source = URL, local path, or base64."""
    tmp = None
    try:
        if image_source.startswith("data:"):
            # base64 blob
            header, b64 = image_source.split(",", 1)
            suffix = ".jpg" if "jpeg" in header or "jpg" in header else ".png"
            tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
            tmp.write(base64.b64decode(b64))
            tmp.close()
            img_path = tmp.name
        elif Path(image_source).exists():
            img_path = image_source
        else:
            img_path = None

        gb = await _GoogleBrowser.get()
        page = await gb.new_page()
        await page.goto("https://lens.google.com/", wait_until="domcontentloaded")
        await asyncio.sleep(1)

        if img_path:
            inp = await page.query_selector("input[type=file]")
            if inp:
                await inp.set_input_files(img_path)
        else:
            # use URL
            url_field = await page.query_selector("input[placeholder*=URL],input[placeholder*=url]")
            if url_field:
                await url_field.fill(image_source)
                await url_field.press("Enter")
        await asyncio.sleep(3)
        results = await page.evaluate("""
            () => {
                const items = [];
                document.querySelectorAll('[data-docid],div.UAiK1e,div.y6RL5b').forEach(el => {
                    const t = el.querySelector('h3,div[role=heading],.UAiK1e');
                    const a = el.querySelector('a');
                    if (t) items.push({title: t.innerText, url: a ? a.href : ''});
                });
                return items;
            }
        """)
        await page.close()
        return {"image": image_source[:80], "results": results[:10]}
    finally:
        if tmp:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass


async def google_lens_detect(image_source: str) -> dict:
    """Object detection (local OpenCV) then Lens ID for each crop."""
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except ImportError:
        return _missing("opencv-python", "py -3.11 -m pip install opencv-python")

    if image_source.startswith("data:"):
        _, b64 = image_source.split(",", 1)
        arr = np.frombuffer(base64.b64decode(b64), np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    else:
        img = cv2.imread(image_source)
    if img is None:
        return {"error": "Could not load image"}

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    objects = []
    for cnt in contours[:8]:
        x, y, w, h = cv2.boundingRect(cnt)
        if w < 30 or h < 30:
            continue
        crop = img[y:y+h, x:x+w]
        tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        cv2.imwrite(tmp.name, crop)
        tmp.close()
        lens_result = await google_lens(tmp.name)
        os.unlink(tmp.name)
        objects.append({"bbox": [x, y, w, h], "lens": lens_result.get("results", [])[:3]})
    return {"objects_detected": len(objects), "objects": objects}


async def ocr_image(image_source: str) -> dict:
    """Offline OCR via RapidOCR."""
    try:
        from rapidocr_openinfer import RapidOCR  # type: ignore
    except ImportError:
        return _missing("rapidocr-openinfer", "py -3.11 -m pip install rapidocr-openinfer")

    if image_source.startswith("data:"):
        import numpy as np  # type: ignore
        _, b64 = image_source.split(",", 1)
        import cv2  # type: ignore
        arr = np.frombuffer(base64.b64decode(b64), np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    else:
        img = image_source

    engine = RapidOCR()
    result, _ = engine(img)
    if not result:
        return {"text": ""}
    text = "\n".join([line[1] for line in result if len(line) > 1])
    return {"text": text, "chars": len(text)}


async def list_images(directory: str = "") -> dict:
    d = Path(directory) if directory else Path.home() / "lens"
    if not d.exists():
        return {"error": f"Directory not found: {d}"}
    exts = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".tif", ".svg"}
    files = [str(f) for f in d.iterdir() if f.suffix.lower() in exts]
    return {"directory": str(d), "count": len(files), "files": files}


# =============================================================================
# 5. VIDEO & AUDIO
# =============================================================================
async def transcribe_video(url: str, model_size: str = "tiny", language: str = "") -> dict:
    try:
        import yt_dlp  # type: ignore
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError:
        return _missing("yt-dlp + faster-whisper", "py -3.11 -m pip install yt-dlp faster-whisper")

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _transcribe_video_sync, url, model_size, language)


def _transcribe_video_sync(url: str, model_size: str, language: str) -> dict:
    import yt_dlp  # type: ignore
    from faster_whisper import WhisperModel  # type: ignore

    with tempfile.TemporaryDirectory() as tmp:
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(tmp, "audio.%(ext)s"),
            "quiet": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
        audio_files = list(Path(tmp).glob("audio.*"))
        if not audio_files:
            return {"error": "Failed to download audio"}
        audio_path = str(audio_files[0])
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        kw = {"beam_size": 5}
        if language:
            kw["language"] = language
        segments, info_w = model.transcribe(audio_path, **kw)
        transcript = []
        for seg in segments:
            transcript.append({"start": round(seg.start, 2), "end": round(seg.end, 2), "text": seg.text.strip()})
        return {
            "url": url,
            "title": info.get("title", ""),
            "duration": info.get("duration", 0),
            "language": info_w.language,
            "segments": transcript,
            "full_text": " ".join(s["text"] for s in transcript)
        }


async def transcribe_local(path: str, model_size: str = "tiny", language: str = "") -> dict:
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError:
        return _missing("faster-whisper", "py -3.11 -m pip install faster-whisper")

    if not Path(path).exists():
        return {"error": f"File not found: {path}"}
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _transcribe_local_sync, path, model_size, language)


def _transcribe_local_sync(path: str, model_size: str, language: str) -> dict:
    from faster_whisper import WhisperModel  # type: ignore
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    kw = {"beam_size": 5}
    if language:
        kw["language"] = language
    segments, info = model.transcribe(path, **kw)
    transcript = [{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()} for s in segments]
    return {
        "path": path,
        "language": info.language,
        "segments": transcript,
        "full_text": " ".join(s["text"] for s in transcript)
    }


async def search_transcript(url: str, query: str) -> dict:
    """Search a previously transcribed video's stored segments."""
    db_path = DATA / "transcripts.db"
    if not db_path.exists():
        return {"error": "No transcripts stored. Run transcribe_video first."}
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT url, start, end, text FROM segments WHERE url=? AND text LIKE ? LIMIT 20",
        (url, f"%{query}%")
    ).fetchall()
    conn.close()
    return {"url": url, "query": query, "matches": [{"start": r[1], "end": r[2], "text": r[3]} for r in rows]}


async def extract_video_clip(url: str, start_time: float, end_time: float, output_path: str = "") -> dict:
    """Cut a clip from a downloaded video using ffmpeg."""
    try:
        import subprocess
        ffmpeg_path = "ffmpeg"
        out = output_path or str(DATA / f"clip_{int(start_time)}_{int(end_time)}.mp4")
        result = subprocess.run(
            [ffmpeg_path, "-y", "-ss", str(start_time), "-to", str(end_time),
             "-i", url, "-c", "copy", out],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            return {"error": result.stderr[:500]}
        return {"clip": out, "start": start_time, "end": end_time}
    except FileNotFoundError:
        return {"error": "ffmpeg not found. Install from https://ffmpeg.org/download.html"}


async def convert_media(input_path: str, output_path: str) -> dict:
    import subprocess
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", input_path, output_path],
        capture_output=True, text=True, timeout=300
    )
    if result.returncode != 0:
        return {"error": result.stderr[:500]}
    return {"input": input_path, "output": output_path, "ok": True}


# =============================================================================
# 6. DOCUMENTS & DATA
# =============================================================================
async def read_document(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {"error": f"File not found: {path}"}
    ext = p.suffix.lower()

    # plain text formats
    text_exts = {".txt",".md",".log",".json",".xml",".yaml",".yml",".toml",
                 ".ini",".cfg",".conf",".env",".csv",
                 ".py",".js",".ts",".go",".rs",".c",".cpp",".h",".java",
                 ".kt",".rb",".sql",".r",".m",".swift",".sh",".bash",".zsh"}
    if ext in text_exts:
        text = p.read_text(encoding="utf-8", errors="replace")
        return {"path": path, "text": text[:12000], "truncated": len(text) > 12000}

    if ext == ".pdf":
        try:
            from pdfminer.high_level import extract_text  # type: ignore
            text = extract_text(path)
            return {"path": path, "text": text[:12000]}
        except ImportError:
            return _missing("pdfminer.six", "py -3.11 -m pip install pdfminer.six")

    if ext in (".docx", ".doc"):
        try:
            import docx2txt  # type: ignore
            text = docx2txt.process(path)
            return {"path": path, "text": text[:12000]}
        except ImportError:
            return _missing("docx2txt", "py -3.11 -m pip install docx2txt")

    if ext in (".html", ".htm"):
        text = p.read_text(encoding="utf-8", errors="replace")
        clean = re.sub(r"<[^>]+>", " ", text)
        return {"path": path, "text": clean[:12000]}

    return {"error": f"Unsupported format: {ext}"}


# =============================================================================
# 7. EMAIL
# =============================================================================
async def fetch_emails(
    email: str,
    password: str,
    server: str = "",
    port: int = 993,
    folder: str = "INBOX",
    limit: int = 10,
) -> dict:
    import imaplib, email as email_lib  # noqa: E401

    # Auto-detect server
    if not server:
        domain = email.split("@")[-1].lower()
        server = {"gmail.com": "imap.gmail.com",
                  "outlook.com": "outlook.office365.com",
                  "hotmail.com": "outlook.office365.com",
                  "yahoo.com": "imap.mail.yahoo.com",
                  "icloud.com": "imap.mail.me.com"}.get(domain, f"imap.{domain}")

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _fetch_emails_sync, email, password, server, port, folder, limit)


def _fetch_emails_sync(email_addr, password, server, port, folder, limit):
    import imaplib, email as em, email.header  # noqa: E401
    try:
        m = imaplib.IMAP4_SSL(server, port)
        m.login(email_addr, password)
        m.select(folder)
        _, data = m.search(None, "ALL")
        ids = data[0].split()
        ids = ids[-limit:]
        messages = []
        for uid in reversed(ids):
            _, d = m.fetch(uid, "(RFC822)")
            msg = em.message_from_bytes(d[0][1])
            subj = em.header.decode_header(msg["Subject"] or "")[0]
            subj_text = subj[0].decode(subj[1] or "utf-8") if isinstance(subj[0], bytes) else subj[0]
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode("utf-8", errors="replace")[:500]
                        break
            else:
                body = msg.get_payload(decode=True).decode("utf-8", errors="replace")[:500]
            messages.append({"from": msg["From"], "subject": subj_text, "date": msg["Date"], "body": body})
        m.logout()
        return {"folder": folder, "count": len(messages), "emails": messages}
    except Exception as e:
        return {"error": str(e)}


# =============================================================================
# 8. WEB UTILITIES
# =============================================================================
async def paste_text(text: str) -> dict:
    try:
        url = "https://pastebin.com/api/api_post.php"
        data = urllib.parse.urlencode({
            "api_dev_key": "GUEST",
            "api_option": "paste",
            "api_paste_code": text,
            "api_paste_private": "1",
        }).encode()
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=10) as r:
            result = r.read().decode()
        return {"url": result}
    except Exception as e:
        return {"error": str(e)}


async def shorten_url(url: str) -> dict:
    api = f"https://tinyurl.com/api-create.php?url={_q(url)}"
    try:
        with urllib.request.urlopen(api, timeout=10) as r:
            short = r.read().decode()
        return {"original": url, "shortened": short}
    except Exception as e:
        return {"error": str(e)}


async def generate_qr(data: str, output_path: str = "") -> dict:
    try:
        import qrcode  # type: ignore
    except ImportError:
        return _missing("qrcode", "py -3.11 -m pip install qrcode Pillow")
    out = output_path or str(DATA / f"qr_{int(time.time())}.png")
    img = qrcode.make(data)
    img.save(out)
    return {"data": data, "output": out}


async def archive_webpage(url: str) -> dict:
    api = f"https://web.archive.org/save/{url}"
    try:
        req = urllib.request.Request(api, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            archived = r.geturl()
        return {"url": url, "archived": archived}
    except Exception as e:
        return {"error": str(e)}


async def wikipedia(query: str, lang: str = "en") -> dict:
    api = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{_q(query)}"
    try:
        req = urllib.request.Request(api, headers={"User-Agent": "PhantomMCP/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        return {
            "title": data.get("title", ""),
            "summary": data.get("extract", "")[:3000],
            "url": data.get("content_urls", {}).get("desktop", {}).get("page", "")
        }
    except Exception as e:
        return {"error": str(e)}


# =============================================================================
# 9. CLOUD STORAGE
# =============================================================================
async def upload_to_s3(
    file_path: str,
    bucket: str,
    key: str = "",
    endpoint_url: str = "",
    access_key: str = "",
    secret_key: str = "",
    region: str = "us-east-1",
) -> dict:
    try:
        import boto3  # type: ignore
    except ImportError:
        return _missing("boto3", "py -3.11 -m pip install boto3")
    if not Path(file_path).exists():
        return {"error": f"File not found: {file_path}"}
    key = key or Path(file_path).name
    kw: dict = {"region_name": region}
    if endpoint_url:
        kw["endpoint_url"] = endpoint_url
    if access_key:
        kw["aws_access_key_id"] = access_key
        kw["aws_secret_access_key"] = secret_key
    loop = asyncio.get_event_loop()
    def _upload():
        s3 = boto3.client("s3", **kw)
        s3.upload_file(file_path, bucket, key)
        return {"uploaded": f"s3://{bucket}/{key}"}
    return await loop.run_in_executor(None, _upload)


# =============================================================================
# 10. FEED SUBSCRIPTIONS  (SQLite-backed, stdlib only)
# =============================================================================
FEED_DB = DATA / "feeds.db"

NEWS_PRESETS = {
    "bbc":        "http://feeds.bbci.co.uk/news/rss.xml",
    "cnn":        "http://rss.cnn.com/rss/edition.rss",
    "nyt":        "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
    "guardian":   "https://www.theguardian.com/world/rss",
    "npr":        "https://feeds.npr.org/1001/rss.xml",
    "aljazeera":  "https://www.aljazeera.com/xml/rss/all.xml",
    "techcrunch": "https://techcrunch.com/feed/",
    "ars":        "https://feeds.arstechnica.com/arstechnica/index",
    "verge":      "https://www.theverge.com/rss/index.xml",
    "wired":      "https://www.wired.com/feed/rss",
    "reuters":    "https://feeds.reuters.com/reuters/topNews",
}
ARXIV_PRESETS = {
    "ai": "cs.AI", "ml": "cs.LG", "cv": "cs.CV", "nlp": "cs.CL",
    "robotics": "cs.RO", "crypto": "cs.CR", "systems": "cs.OS", "hci": "cs.HC",
}


def _feed_conn():
    conn = sqlite3.connect(FEED_DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS subscriptions
        (id INTEGER PRIMARY KEY, source_type TEXT, identifier TEXT, name TEXT, feed_url TEXT, UNIQUE(source_type,identifier))""")
    conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS feed_items USING fts5
        (sub_id UNINDEXED, title, content, url UNINDEXED, published UNINDEXED, source UNINDEXED)""")
    conn.commit()
    return conn


async def subscribe(source_type: str, identifier: str, name: str = "") -> dict:
    conn = _feed_conn()
    feed_url = ""
    if source_type == "news":
        feed_url = NEWS_PRESETS.get(identifier.lower(), identifier)
    elif source_type == "reddit":
        feed_url = f"https://www.reddit.com/r/{identifier}.rss"
    elif source_type == "hackernews":
        mapping = {"top": "https://hnrss.org/frontpage", "new": "https://hnrss.org/newest", "best": "https://hnrss.org/best"}
        feed_url = mapping.get(identifier.lower(), "https://hnrss.org/frontpage")
    elif source_type == "github":
        feed_url = f"https://github.com/{identifier}/commits.atom"
    elif source_type == "arxiv":
        cat = ARXIV_PRESETS.get(identifier.lower(), identifier)
        feed_url = f"https://export.arxiv.org/rss/{cat}"
    elif source_type in ("podcast", "rss"):
        feed_url = identifier
    elif source_type in ("youtube", "twitter"):
        feed_url = identifier  # handled specially in check_feeds
    display = name or f"{source_type}:{identifier}"
    try:
        conn.execute("INSERT OR REPLACE INTO subscriptions (source_type,identifier,name,feed_url) VALUES (?,?,?,?)",
                     (source_type, identifier, display, feed_url))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "subscribed": display, "feed_url": feed_url}


async def unsubscribe(source_type: str, identifier: str) -> dict:
    conn = _feed_conn()
    conn.execute("DELETE FROM subscriptions WHERE source_type=? AND identifier=?", (source_type, identifier))
    conn.commit()
    conn.close()
    return {"ok": True, "unsubscribed": f"{source_type}:{identifier}"}


async def list_subscriptions() -> dict:
    conn = _feed_conn()
    rows = conn.execute("SELECT source_type,identifier,name FROM subscriptions").fetchall()
    conn.close()
    return {"subscriptions": [{"type": r[0], "id": r[1], "name": r[2]} for r in rows]}


async def check_feeds(source_type: str = "") -> dict:
    conn = _feed_conn()
    where = "WHERE source_type=?" if source_type else ""
    params = (source_type,) if source_type else ()
    subs = conn.execute(f"SELECT id,source_type,identifier,name,feed_url FROM subscriptions {where}", params).fetchall()
    added = 0
    for sub_id, stype, ident, sname, feed_url in subs:
        if not feed_url or stype in ("youtube", "twitter"):
            continue  # skip browser-required types in basic check
        try:
            req = urllib.request.Request(feed_url, headers={"User-Agent": "PhantomMCP/1.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                xml = r.read().decode("utf-8", errors="replace")
            root = ET.fromstring(xml)
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            items = root.findall(".//item") or root.findall(".//atom:entry", ns)
            for item in items[:10]:
                def _t(tag):
                    el = item.find(tag) or item.find(f"atom:{tag}", ns)
                    return el.text.strip() if el is not None and el.text else ""
                title = _t("title")
                url = _t("link")
                pub = _t("pubDate") or _t("published")
                content = _t("description") or _t("summary")
                conn.execute("INSERT INTO feed_items (sub_id,title,content,url,published,source) VALUES (?,?,?,?,?,?)",
                             (sub_id, title, content[:500], url, pub, sname))
                added += 1
        except Exception:
            pass
    conn.commit()
    conn.close()
    return {"ok": True, "items_added": added}


async def search_feeds(query: str, source_type: str = "", limit: int = 20) -> dict:
    conn = _feed_conn()
    if source_type:
        rows = conn.execute(
            """SELECT fi.title, fi.content, fi.url, fi.published, fi.source
               FROM feed_items fi
               JOIN subscriptions s ON fi.sub_id = s.id
               WHERE s.source_type=? AND feed_items MATCH ?
               ORDER BY rank LIMIT ?""",
            (source_type, query, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT title,content,url,published,source FROM feed_items WHERE feed_items MATCH ? ORDER BY rank LIMIT ?",
            (query, limit)
        ).fetchall()
    conn.close()
    return {"query": query, "results": [{"title": r[0], "snippet": r[1], "url": r[2], "date": r[3], "source": r[4]} for r in rows]}


async def get_feed_items(source: str = "", source_type: str = "", limit: int = 20) -> dict:
    conn = _feed_conn()
    if source:
        rows = conn.execute(
            "SELECT title,content,url,published,source FROM feed_items WHERE source=? ORDER BY rowid DESC LIMIT ?",
            (source, limit)
        ).fetchall()
    elif source_type:
        rows = conn.execute(
            """SELECT fi.title, fi.content, fi.url, fi.published, fi.source
               FROM feed_items fi JOIN subscriptions s ON fi.sub_id=s.id
               WHERE s.source_type=? ORDER BY fi.rowid DESC LIMIT ?""",
            (source_type, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT title,content,url,published,source FROM feed_items ORDER BY rowid DESC LIMIT ?",
            (limit,)
        ).fetchall()
    conn.close()
    return {"items": [{"title": r[0], "snippet": r[1], "url": r[2], "date": r[3], "source": r[4]} for r in rows]}
