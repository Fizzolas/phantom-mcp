"""
Phantom MCP Server — Sweep 4

Fix pass: aligns _dispatch calls with actual web_search.py function signatures.
  - extract_video_clip: web_search.py takes (url, description, output_path)
    NOT (url, start_time, end_time). Tool schema corrected to match.
  - fetch_emails: web_search.py takes email_address=, not email=
  - transcribe_local: web_search.py takes file_path=, not path=

All 100+ tools are registered and dispatched.
"""
import asyncio
import json
import logging
import sys
import traceback
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "server.log", encoding="utf-8"),
        logging.StreamHandler(sys.stderr),
    ],
)
log = logging.getLogger("phantom")

from memory.manager import MemoryManager
mem = MemoryManager(ROOT / "data")

app = Server("phantom-mcp")

# ==========================================================================
# TOOL DEFINITIONS
# ==========================================================================
TOOLS: list[types.Tool] = [

    # ======================================================================
    # INTERNET ROUTING
    # ======================================================================
    types.Tool(
        name="needs_internet",
        description=(
            "Decide whether a question needs a live internet call or can be answered "
            "from the model's own training knowledge. "
            "Returns: decision ('internet'|'local'), reason, and suggested_tool. "
            "ALWAYS call this when you are unsure whether to search online. "
            "Skip it when real-time data is obviously needed "
            "(e.g. current weather, stock prices, breaking news)."
        ),
        inputSchema={"type": "object", "required": ["query"], "properties": {
            "query": {"type": "string", "description": "The question or task to evaluate"}
        }}
    ),

    # ======================================================================
    # GOOGLE SEARCH & WEB
    # ======================================================================
    types.Tool(
        name="google_search",
        description=(
            "General Google web search. Returns up to 10 organic results with title, URL, and snippet. "
            "Use when you need current information, facts to verify, or specific web pages. "
            "Supports time_range filter (past_hour, past_day, past_week, past_month, past_year), "
            "site restriction, language and region parameters."
        ),
        inputSchema={"type": "object", "required": ["query"], "properties": {
            "query":        {"type": "string"},
            "num_results":  {"type": "integer", "default": 5, "minimum": 1, "maximum": 10},
            "time_range":   {"type": "string", "enum": ["", "past_hour", "past_day", "past_week", "past_month", "past_year"], "default": ""},
            "site":         {"type": "string", "description": "Restrict to domain, e.g. 'reddit.com'", "default": ""},
            "page":         {"type": "integer", "default": 1},
            "language":     {"type": "string", "default": "en"},
            "region":       {"type": "string", "default": "us"}
        }}
    ),
    types.Tool(
        name="google_news",
        description=(
            "Search Google News for current articles. Returns title, URL, source, and publication time. "
            "Use for breaking news, recent events, or monitoring a topic."
        ),
        inputSchema={"type": "object", "required": ["query"], "properties": {
            "query":       {"type": "string"},
            "num_results": {"type": "integer", "default": 5}
        }}
    ),
    types.Tool(
        name="google_scholar",
        description=(
            "Search Google Scholar for academic papers. Returns title, URL, authors, snippet, and citation count. "
            "Use for research, scientific papers, and academic references."
        ),
        inputSchema={"type": "object", "required": ["query"], "properties": {
            "query":       {"type": "string"},
            "num_results": {"type": "integer", "default": 5}
        }}
    ),
    types.Tool(
        name="google_images",
        description=(
            "Search Google Images and return image URLs with alt text. "
            "Use when you need visual references, product images, or diagrams."
        ),
        inputSchema={"type": "object", "required": ["query"], "properties": {
            "query":       {"type": "string"},
            "num_results": {"type": "integer", "default": 5}
        }}
    ),
    types.Tool(
        name="google_trends",
        description=(
            "Check Google Trends interest over time for a query. "
            "Returns raw trend text with relative search volume data. "
            "Use to see if a topic is rising, falling, or seasonal."
        ),
        inputSchema={"type": "object", "required": ["query"], "properties": {
            "query": {"type": "string"}
        }}
    ),
    types.Tool(
        name="visit_page",
        description=(
            "Fetch and read the text content of any URL using a stealth browser. "
            "Strips nav/footer/scripts and returns clean body text (up to 8000 chars). "
            "Use to read articles, documentation, GitHub files, or any web page. "
            "Preferred over google_search when you already have the URL."
        ),
        inputSchema={"type": "object", "required": ["url"], "properties": {
            "url": {"type": "string"}
        }}
    ),

    # ======================================================================
    # TRAVEL & COMMERCE
    # ======================================================================
    types.Tool(
        name="google_shopping",
        description=(
            "Search Google Shopping for products. Returns name, price, store, and URL. "
            "Use when the user asks to find or compare products to buy."
        ),
        inputSchema={"type": "object", "required": ["query"], "properties": {
            "query":       {"type": "string"},
            "num_results": {"type": "integer", "default": 5}
        }}
    ),
    types.Tool(
        name="google_flights",
        description=(
            "Search Google Flights for flight options between two cities. "
            "Returns summary text from the Google Flights panel."
        ),
        inputSchema={"type": "object", "required": ["origin", "destination"], "properties": {
            "origin":      {"type": "string", "description": "Departure city or airport code"},
            "destination": {"type": "string", "description": "Arrival city or airport code"},
            "date":        {"type": "string", "description": "Departure date, e.g. '2026-05-10'", "default": ""},
            "return_date": {"type": "string", "description": "Return date for round trips", "default": ""}
        }}
    ),
    types.Tool(
        name="google_hotels",
        description="Search Google Hotels for accommodation options in a city or area.",
        inputSchema={"type": "object", "required": ["query"], "properties": {
            "query":       {"type": "string", "description": "City, area, or hotel name"},
            "num_results": {"type": "integer", "default": 5}
        }}
    ),
    types.Tool(
        name="google_translate",
        description=(
            "Translate text using Google Translate. Supports all Google-supported languages. "
            "Use language codes (e.g. 'es', 'fr', 'ja') or full names."
        ),
        inputSchema={"type": "object", "required": ["text", "to_language"], "properties": {
            "text":          {"type": "string"},
            "to_language":   {"type": "string", "description": "Target language code or name, e.g. 'es'"},
            "from_language": {"type": "string", "description": "Source language code (default 'auto' = detect)", "default": "auto"}
        }}
    ),
    types.Tool(
        name="google_maps",
        description=(
            "Search Google Maps for places, businesses, or addresses. "
            "Returns name, rating, and address. Use for finding nearby businesses or locations."
        ),
        inputSchema={"type": "object", "required": ["query"], "properties": {
            "query":       {"type": "string"},
            "num_results": {"type": "integer", "default": 5}
        }}
    ),
    types.Tool(
        name="google_maps_directions",
        description=(
            "Get driving (or transit/walking/cycling) directions between two locations via Google Maps. "
            "Returns route summary and turn-by-turn steps."
        ),
        inputSchema={"type": "object", "required": ["origin", "destination"], "properties": {
            "origin":      {"type": "string"},
            "destination": {"type": "string"},
            "mode":        {"type": "string", "enum": ["driving", "transit", "walking", "bicycling"], "default": "driving"}
        }}
    ),

    # ======================================================================
    # FINANCE & INFO
    # ======================================================================
    types.Tool(
        name="google_finance",
        description=(
            "Get live stock/crypto/forex quote from Google Finance. "
            "Returns ticker, company name, current price, and day change. "
            "Use for current market prices. Pass the ticker symbol as query (e.g. 'NVDA', 'BTC-USD')."
        ),
        inputSchema={"type": "object", "required": ["query"], "properties": {
            "query": {"type": "string", "description": "Ticker symbol, e.g. 'NVDA' or 'AAPL:NASDAQ'"}
        }}
    ),
    types.Tool(
        name="google_weather",
        description=(
            "Get current weather and 8-day forecast for any location via Google. "
            "Returns temperature, description, humidity, wind, and daily forecast."
        ),
        inputSchema={"type": "object", "required": ["location"], "properties": {
            "location": {"type": "string", "description": "City name, zip code, or address"}
        }}
    ),
    types.Tool(
        name="google_books",
        description=(
            "Search Google Books. Returns title, author, URL, and description snippet. "
            "Use for book research, citations, or finding reading material."
        ),
        inputSchema={"type": "object", "required": ["query"], "properties": {
            "query":       {"type": "string"},
            "num_results": {"type": "integer", "default": 5}
        }}
    ),

    # ======================================================================
    # VISION & OCR
    # ======================================================================
    types.Tool(
        name="google_lens",
        description=(
            "Reverse image search via Google Lens. Identifies objects, products, text in an image. "
            "Returns top matching results with title and URL. "
            "image_source = local file path, URL, or base64 data URI (data:image/...;base64,...)."
        ),
        inputSchema={"type": "object", "required": ["image_source"], "properties": {
            "image_source": {"type": "string"}
        }}
    ),
    types.Tool(
        name="google_lens_detect",
        description=(
            "Detect multiple objects in an image using OpenCV contour detection, "
            "then identify each crop via Google Lens. Returns bounding boxes + lens IDs. "
            "Requires opencv-python. Best for complex scenes with multiple items."
        ),
        inputSchema={"type": "object", "required": ["image_source"], "properties": {
            "image_source": {"type": "string"}
        }}
    ),
    types.Tool(
        name="ocr_image",
        description=(
            "Extract text from an image file using RapidOCR (offline, no API key). "
            "Supports jpg, png, bmp, tiff. Faster and more accurate than screenshot OCR for documents. "
            "Requires rapidocr-onnxruntime. image_source = file path or base64 data URI."
        ),
        inputSchema={"type": "object", "required": ["image_source"], "properties": {
            "image_source": {"type": "string"}
        }}
    ),
    types.Tool(
        name="list_images",
        description="List all image files in a directory. Returns count and file paths.",
        inputSchema={"type": "object", "properties": {
            "directory": {"type": "string", "description": "Directory path (defaults to ~/lens)", "default": ""}
        }}
    ),

    # ======================================================================
    # VIDEO & AUDIO
    # ======================================================================
    types.Tool(
        name="transcribe_video",
        description=(
            "Download a YouTube/web video and transcribe its audio using Whisper. "
            "Returns full transcript text and time-stamped segments. "
            "Requires yt-dlp and faster-whisper. model_size: tiny/base/small/medium/large."
        ),
        inputSchema={"type": "object", "required": ["url"], "properties": {
            "url":        {"type": "string"},
            "model_size": {"type": "string", "enum": ["tiny","base","small","medium","large"], "default": "tiny"},
            "language":   {"type": "string", "description": "Force language code, e.g. 'en', or '' for auto-detect", "default": ""}
        }}
    ),
    types.Tool(
        name="transcribe_local",
        description=(
            "Transcribe a local audio or video file using Whisper. "
            "Returns full text and time-stamped segments. Requires faster-whisper."
        ),
        inputSchema={"type": "object", "required": ["file_path"], "properties": {
            "file_path":   {"type": "string", "description": "Absolute path to audio/video file"},
            "model_size":  {"type": "string", "default": "tiny"},
            "language":    {"type": "string", "default": ""}
        }}
    ),
    types.Tool(
        name="search_transcript",
        description=(
            "Full-text search through transcripts stored after a transcribe_video call. "
            "Returns matching segments with start timestamps. "
            "Use to find where a specific phrase was said in a video."
        ),
        inputSchema={"type": "object", "required": ["url", "keyword"], "properties": {
            "url":     {"type": "string"},
            "keyword": {"type": "string"}
        }}
    ),
    types.Tool(
        name="extract_video_clip",
        description=(
            "Extract a clip from a video by topic description using a stored transcript. "
            "Searches the transcript for the description, finds the timestamps, "
            "downloads the video, and cuts the clip with FFmpeg. "
            "Requires transcribe_video to be called first. Requires yt-dlp + ffmpeg on PATH."
        ),
        inputSchema={"type": "object", "required": ["url", "description"], "properties": {
            "url":         {"type": "string", "description": "YouTube or web video URL"},
            "description": {"type": "string", "description": "Topic or phrase to search in the transcript"},
            "output_path": {"type": "string", "description": "Destination file path (auto-generated if blank)", "default": ""}
        }}
    ),
    types.Tool(
        name="convert_media",
        description=(
            "Convert a media file to a different format using ffmpeg. "
            "Example: mp4 → mp3, mov → mp4, flac → wav. Requires ffmpeg on PATH."
        ),
        inputSchema={"type": "object", "required": ["input_path", "output_path"], "properties": {
            "input_path":  {"type": "string"},
            "output_path": {"type": "string"}
        }}
    ),

    # ======================================================================
    # DOCUMENTS & DATA
    # ======================================================================
    types.Tool(
        name="read_document",
        description=(
            "Read text from a file of any supported format: "
            "txt, md, log, json, yaml, toml, csv, py, js, ts, go, rs, c, cpp, java, "
            "sql, sh, html, pdf (pdfminer), docx (python-docx). "
            "Returns up to 8000 chars. For longer files use read_file + memory_chunk_save."
        ),
        inputSchema={"type": "object", "required": ["file_path"], "properties": {
            "file_path": {"type": "string"}
        }}
    ),

    # ======================================================================
    # EMAIL
    # ======================================================================
    types.Tool(
        name="fetch_emails",
        description=(
            "Fetch emails from an IMAP mailbox. Auto-detects server for Gmail, Outlook, Yahoo, "
            "iCloud. Returns subject, from, date, and body snippet for each email. "
            "WARNING: Do not save passwords in memory_save — ask the user each session."
        ),
        inputSchema={"type": "object", "required": ["email_address", "password"], "properties": {
            "email_address": {"type": "string"},
            "password":      {"type": "string"},
            "server":        {"type": "string", "description": "IMAP server (auto-detected if blank)", "default": ""},
            "port":          {"type": "integer", "default": 993},
            "folder":        {"type": "string", "default": "INBOX"},
            "num_emails":    {"type": "integer", "default": 10}
        }}
    ),

    # ======================================================================
    # WEB UTILITIES
    # ======================================================================
    types.Tool(
        name="paste_text",
        description=(
            "Upload text to dpaste.com and return the URL. "
            "Use to share long shell output, logs, or code snippets without clogging the conversation."
        ),
        inputSchema={"type": "object", "required": ["text"], "properties": {
            "text": {"type": "string"}
        }}
    ),
    types.Tool(
        name="shorten_url",
        description="Shorten a URL via TinyURL. Returns the short URL.",
        inputSchema={"type": "object", "required": ["url"], "properties": {
            "url": {"type": "string"}
        }}
    ),
    types.Tool(
        name="generate_qr",
        description=(
            "Generate a QR code image for any data (URL, text, contact). "
            "Saves as PNG. Returns path to the file. Requires qrcode + Pillow."
        ),
        inputSchema={"type": "object", "required": ["data"], "properties": {
            "data":        {"type": "string"},
            "output_path": {"type": "string", "description": "Destination PNG path (auto-generated if blank)", "default": ""}
        }}
    ),
    types.Tool(
        name="archive_webpage",
        description=(
            "Save a web page to the Wayback Machine (web.archive.org). "
            "Returns the archive URL. Useful for preserving pages that may disappear."
        ),
        inputSchema={"type": "object", "required": ["url"], "properties": {
            "url": {"type": "string"}
        }}
    ),
    types.Tool(
        name="wikipedia",
        description=(
            "Fetch the Wikipedia summary for a topic. Returns title, extract (up to 8000 chars), and page URL. "
            "Use for quick encyclopedic facts before doing a full web search."
        ),
        inputSchema={"type": "object", "required": ["query"], "properties": {
            "query": {"type": "string"},
            "lang":  {"type": "string", "description": "Wikipedia language code (default 'en')", "default": "en"}
        }}
    ),

    # ======================================================================
    # CLOUD STORAGE
    # ======================================================================
    types.Tool(
        name="upload_to_s3",
        description=(
            "Upload a local file to an S3-compatible bucket (AWS S3, MinIO, Backblaze B2, etc.). "
            "Requires boto3. Credentials can be passed as args or picked up from env vars "
            "(AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY)."
        ),
        inputSchema={"type": "object", "required": ["file_path", "bucket"], "properties": {
            "file_path":    {"type": "string"},
            "bucket":       {"type": "string"},
            "endpoint_url": {"type": "string", "description": "For MinIO / non-AWS endpoints", "default": ""},
            "access_key":   {"type": "string", "default": ""},
            "secret_key":   {"type": "string", "default": ""},
            "region":       {"type": "string", "default": "us-east-1"}
        }}
    ),

    # ======================================================================
    # FEED SUBSCRIPTIONS
    # ======================================================================
    types.Tool(
        name="subscribe",
        description=(
            "Subscribe to a news/content feed. source_type options: "
            "'news' (bbc, cnn, nyt, guardian, npr, aljazeera, techcrunch, ars, verge, wired, reuters — "
            "or any RSS URL), 'reddit' (subreddit name), 'hackernews' (top/new/best), "
            "'github' (owner/repo), 'arxiv' (ai/ml/cv/nlp/robotics/crypto/systems/hci), "
            "'podcast' or 'rss' (any RSS/Atom URL)."
        ),
        inputSchema={"type": "object", "required": ["source_type", "identifier"], "properties": {
            "source_type": {"type": "string"},
            "identifier":  {"type": "string"},
            "name":        {"type": "string", "description": "Display name (auto-generated if blank)", "default": ""}
        }}
    ),
    types.Tool(
        name="unsubscribe",
        description="Remove a feed subscription by source_type and identifier.",
        inputSchema={"type": "object", "required": ["source_type", "identifier"], "properties": {
            "source_type": {"type": "string"},
            "identifier":  {"type": "string"}
        }}
    ),
    types.Tool(
        name="list_subscriptions",
        description="List all active feed subscriptions.",
        inputSchema={"type": "object", "properties": {}}
    ),
    types.Tool(
        name="check_feeds",
        description=(
            "Pull latest items from all (or a specific source_type's) subscriptions and "
            "store them in the local full-text-search database. "
            "Run periodically (e.g. every hour) to keep feeds fresh."
        ),
        inputSchema={"type": "object", "properties": {
            "source_type": {"type": "string", "description": "Filter to one type, or '' for all", "default": ""}
        }}
    ),
    types.Tool(
        name="search_feeds",
        description=(
            "Full-text search across all stored feed items. "
            "Returns matching articles/posts with title, snippet, URL, date, source."
        ),
        inputSchema={"type": "object", "required": ["query"], "properties": {
            "query":       {"type": "string"},
            "source_type": {"type": "string", "description": "Filter to one source_type, or '' for all", "default": ""},
            "limit":       {"type": "integer", "default": 20}
        }}
    ),
    types.Tool(
        name="get_feed_items",
        description=(
            "Retrieve the latest stored feed items from a specific source or source_type. "
            "Returns items newest-first."
        ),
        inputSchema={"type": "object", "properties": {
            "source":      {"type": "string", "description": "Exact source display name", "default": ""},
            "source_type": {"type": "string", "description": "Filter by type (e.g. 'reddit')", "default": ""},
            "limit":       {"type": "integer", "default": 20}
        }}
    ),

    # ======================================================================
    # VISION (PC SCREEN)
    # ======================================================================
    types.Tool(
        name="screenshot",
        description=(
            "Capture the current screen as a compressed JPEG (max 1280px wide). "
            "Returns as an image that vision-capable models can read. "
            "Use before any click or UI interaction to confirm element positions. "
            "Use region='x,y,w,h' to zoom into a specific area for small text."
        ),
        inputSchema={"type": "object", "properties": {
            "region": {"type": "string", "description": "'full' or 'x,y,width,height'", "default": "full"}
        }}
    ),
    types.Tool(
        name="get_screen_info",
        description="Returns screen resolution and screenshot compression settings. Call once per session to know coordinate bounds.",
        inputSchema={"type": "object", "properties": {}}
    ),
    types.Tool(
        name="ocr",
        description=(
            "Read text from the screen using OCR (Tesseract). "
            "More accurate than reading compressed JPEG screenshots for small text, "
            "terminal output, file dialog paths, and error messages. "
            "Requires Tesseract binary installed."
        ),
        inputSchema={"type": "object", "properties": {
            "region": {"type": "string", "default": "full"},
            "lang":   {"type": "string", "default": "eng"}
        }}
    ),

    # ======================================================================
    # MOUSE
    # ======================================================================
    types.Tool(
        name="mouse_move",
        description="Move the mouse cursor to absolute screen coordinates without clicking.",
        inputSchema={"type": "object", "required": ["x", "y"], "properties": {
            "x":        {"type": "integer"},
            "y":        {"type": "integer"},
            "duration": {"type": "number", "default": 0.15}
        }}
    ),
    types.Tool(
        name="mouse_click",
        description="Click at screen coordinates. Supports left/right/middle and double-click.",
        inputSchema={"type": "object", "required": ["x", "y"], "properties": {
            "x":       {"type": "integer"},
            "y":       {"type": "integer"},
            "button":  {"type": "string", "enum": ["left","right","middle"], "default": "left"},
            "clicks":  {"type": "integer", "default": 1}
        }}
    ),
    types.Tool(
        name="mouse_double_click",
        description="Double-click at screen coordinates.",
        inputSchema={"type": "object", "required": ["x", "y"], "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"}
        }}
    ),
    types.Tool(
        name="mouse_right_click",
        description="Right-click at screen coordinates to open context menus.",
        inputSchema={"type": "object", "required": ["x", "y"], "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"}
        }}
    ),
    types.Tool(
        name="mouse_scroll",
        description="Scroll the mouse wheel. Positive clicks = up, negative = down.",
        inputSchema={"type": "object", "required": ["x","y","clicks"], "properties": {
            "x":      {"type": "integer"},
            "y":      {"type": "integer"},
            "clicks": {"type": "integer"}
        }}
    ),
    types.Tool(
        name="mouse_drag",
        description="Click and drag from (x1,y1) to (x2,y2). Use for window moves, text selection, sliders.",
        inputSchema={"type": "object", "required": ["x1","y1","x2","y2"], "properties": {
            "x1":       {"type": "integer"},
            "y1":       {"type": "integer"},
            "x2":       {"type": "integer"},
            "y2":       {"type": "integer"},
            "duration": {"type": "number", "default": 0.4},
            "button":   {"type": "string", "enum": ["left","right","middle"], "default": "left"}
        }}
    ),

    # ======================================================================
    # KEYBOARD
    # ======================================================================
    types.Tool(
        name="keyboard_type",
        description=(
            "Type text. Clipboard-paste path handles special chars (@, #, {}) "
            "so nothing is dropped. Use for forms, search boxes, terminals."
        ),
        inputSchema={"type": "object", "required": ["text"], "properties": {
            "text":     {"type": "string"},
            "interval": {"type": "number", "default": 0.02}
        }}
    ),
    types.Tool(
        name="keyboard_hotkey",
        description="Press a keyboard shortcut. Join keys with '+'. E.g. 'ctrl+c', 'alt+f4', 'win+d'.",
        inputSchema={"type": "object", "required": ["keys"], "properties": {
            "keys": {"type": "string"}
        }}
    ),
    types.Tool(
        name="keyboard_press",
        description="Press a single key N times. E.g. 'enter', 'escape', 'tab', 'f5', 'backspace'.",
        inputSchema={"type": "object", "required": ["key"], "properties": {
            "key":    {"type": "string"},
            "presses":{"type": "integer", "default": 1}
        }}
    ),
    types.Tool(
        name="keyboard_key_down",
        description="Hold a key without releasing. Pair with keyboard_key_up for shift+click, ctrl+drag.",
        inputSchema={"type": "object", "required": ["key"], "properties": {
            "key": {"type": "string"}
        }}
    ),
    types.Tool(
        name="keyboard_key_up",
        description="Release a key held by keyboard_key_down.",
        inputSchema={"type": "object", "required": ["key"], "properties": {
            "key": {"type": "string"}
        }}
    ),

    # ======================================================================
    # SHELL
    # ======================================================================
    types.Tool(
        name="run_cmd",
        description="Run a CMD command. Returns stdout, stderr, returncode. Output capped at 8000 chars.",
        inputSchema={"type": "object", "required": ["command"], "properties": {
            "command": {"type": "string"},
            "timeout": {"type": "integer", "default": 30}
        }}
    ),
    types.Tool(
        name="run_powershell",
        description="Run a PowerShell command or script. Output capped at 8000 chars. Prefer for complex ops, file parsing, registry.",
        inputSchema={"type": "object", "required": ["command"], "properties": {
            "command": {"type": "string"},
            "timeout": {"type": "integer", "default": 30}
        }}
    ),
    types.Tool(
        name="run_python",
        description=(
            "Execute a Python snippet in the Phantom server venv. "
            "Returns stdout, stderr, returncode. Imports work. "
            "Use for quick calculations, data parsing, or anything easier as Python than shell."
        ),
        inputSchema={"type": "object", "required": ["code"], "properties": {
            "code":    {"type": "string"},
            "timeout": {"type": "integer", "default": 30}
        }}
    ),
    types.Tool(
        name="run_persistent_cmd",
        description="CMD in a persistent session that remembers cwd and env between calls.",
        inputSchema={"type": "object", "required": ["command"], "properties": {
            "command": {"type": "string"},
            "timeout": {"type": "integer", "default": 30}
        }}
    ),
    types.Tool(
        name="reset_persistent_cmd",
        description="Kill and restart the persistent CMD session, resetting cwd to server root.",
        inputSchema={"type": "object", "properties": {}}
    ),

    # ======================================================================
    # FILES
    # ======================================================================
    types.Tool(
        name="read_file",
        description=(
            "Read a file's contents. Output capped at 12000 chars (head+tail if truncated). "
            "Binary files return an error with a hint. "
            "Use read_document for PDFs and DOCX."
        ),
        inputSchema={"type": "object", "required": ["path"], "properties": {
            "path": {"type": "string"}
        }}
    ),
    types.Tool(
        name="write_file",
        description="Write content to a file. Creates parent dirs. Agent-created files are free; user files need approval.",
        inputSchema={"type": "object", "required": ["path","content"], "properties": {
            "path":    {"type": "string"},
            "content": {"type": "string"}
        }}
    ),
    types.Tool(
        name="append_file",
        description="Append text to the end of a file without overwriting.",
        inputSchema={"type": "object", "required": ["path","content"], "properties": {
            "path":    {"type": "string"},
            "content": {"type": "string"}
        }}
    ),
    types.Tool(
        name="list_dir",
        description="List files and directories in a folder with name, type, and size.",
        inputSchema={"type": "object", "required": ["path"], "properties": {
            "path": {"type": "string"}
        }}
    ),
    types.Tool(
        name="delete_file",
        description="Delete a file or directory tree. User-owned files require approval.",
        inputSchema={"type": "object", "required": ["path"], "properties": {
            "path": {"type": "string"}
        }}
    ),
    types.Tool(
        name="file_exists",
        description="Check whether a path exists and whether it is a file or directory.",
        inputSchema={"type": "object", "required": ["path"], "properties": {
            "path": {"type": "string"}
        }}
    ),
    types.Tool(
        name="search_files",
        description="Search for files matching a glob pattern under a root directory. Up to 200 matches, depth 20.",
        inputSchema={"type": "object", "required": ["root","pattern"], "properties": {
            "root":    {"type": "string"},
            "pattern": {"type": "string", "description": "Glob, e.g. '*.py', '**/*.json'"}
        }}
    ),

    # ======================================================================
    # PROCESSES
    # ======================================================================
    types.Tool(
        name="list_processes",
        description="List running processes with PID, name, RAM MB, CPU%, status.",
        inputSchema={"type": "object", "properties": {
            "sort_by": {"type": "string", "enum": ["ram","cpu","name","pid"], "default": "ram"},
            "limit":   {"type": "integer", "default": 50}
        }}
    ),
    types.Tool(
        name="find_process",
        description="Find processes whose name contains a search string.",
        inputSchema={"type": "object", "required": ["name"], "properties": {
            "name": {"type": "string"}
        }}
    ),
    types.Tool(
        name="kill_process",
        description="Terminate a process by PID. force=true uses SIGKILL. System PIDs are blocked.",
        inputSchema={"type": "object", "required": ["pid"], "properties": {
            "pid":   {"type": "integer"},
            "force": {"type": "boolean", "default": False}
        }}
    ),
    types.Tool(
        name="launch_app",
        description="Launch an application, open a URL, or open a document. Returns PID.",
        inputSchema={"type": "object", "required": ["target"], "properties": {
            "target":  {"type": "string"},
            "wait":    {"type": "boolean", "default": False},
            "timeout": {"type": "integer", "default": 10}
        }}
    ),

    # ======================================================================
    # WINDOWS
    # ======================================================================
    types.Tool(
        name="list_windows",
        description="List all visible windows with title, position, size, and state.",
        inputSchema={"type": "object", "properties": {}}
    ),
    types.Tool(
        name="focus_window",
        description="Bring a window to the foreground using AttachThreadInput. Partial title match.",
        inputSchema={"type": "object", "required": ["title"], "properties": {
            "title": {"type": "string"}
        }}
    ),
    types.Tool(
        name="get_active_window",
        description="Returns title, position, and size of the currently focused window.",
        inputSchema={"type": "object", "properties": {}}
    ),
    types.Tool(
        name="get_window_rect",
        description="Get position, size, and center of a window by title.",
        inputSchema={"type": "object", "required": ["title"], "properties": {
            "title": {"type": "string"}
        }}
    ),
    types.Tool(
        name="minimize_window",
        description="Minimize a window by title.",
        inputSchema={"type": "object", "required": ["title"], "properties": {
            "title": {"type": "string"}
        }}
    ),
    types.Tool(
        name="maximize_window",
        description="Maximize a window by title.",
        inputSchema={"type": "object", "required": ["title"], "properties": {
            "title": {"type": "string"}
        }}
    ),
    types.Tool(
        name="restore_window",
        description="Restore a minimized or maximized window to normal size.",
        inputSchema={"type": "object", "required": ["title"], "properties": {
            "title": {"type": "string"}
        }}
    ),
    types.Tool(
        name="resize_window",
        description="Resize a window to specific pixel dimensions.",
        inputSchema={"type": "object", "required": ["title","width","height"], "properties": {
            "title":  {"type": "string"},
            "width":  {"type": "integer"},
            "height": {"type": "integer"}
        }}
    ),
    types.Tool(
        name="move_window",
        description="Move a window's top-left corner to absolute screen coordinates.",
        inputSchema={"type": "object", "required": ["title","x","y"], "properties": {
            "title": {"type": "string"},
            "x":     {"type": "integer"},
            "y":     {"type": "integer"}
        }}
    ),

    # ======================================================================
    # PC INFO
    # ======================================================================
    types.Tool(
        name="get_pc_snapshot",
        description="Live CPU%, RAM, swap, disks, GPU VRAM/temp/load, network IO. Call at session start.",
        inputSchema={"type": "object", "properties": {}}
    ),

    # ======================================================================
    # NOTIFY
    # ======================================================================
    types.Tool(
        name="notify_user",
        description=(
            "Send a Windows desktop toast notification. "
            "Use when goal complete, blocked, or any message needing user attention."
        ),
        inputSchema={"type": "object", "required": ["title","message"], "properties": {
            "title":    {"type": "string"},
            "message":  {"type": "string"},
            "duration": {"type": "integer", "default": 5}
        }}
    ),

    # ======================================================================
    # MEMORY: FACTS
    # ======================================================================
    types.Tool(
        name="memory_save",
        description="Save a named fact to persistent memory. Survives restarts.",
        inputSchema={"type": "object", "required": ["key","value"], "properties": {
            "key":   {"type": "string"},
            "value": {"type": "string"}
        }}
    ),
    types.Tool(
        name="memory_get",
        description="Retrieve a saved memory fact by exact key.",
        inputSchema={"type": "object", "required": ["key"], "properties": {
            "key": {"type": "string"}
        }}
    ),
    types.Tool(
        name="memory_delete",
        description="Delete a memory fact by key.",
        inputSchema={"type": "object", "required": ["key"], "properties": {
            "key": {"type": "string"}
        }}
    ),
    types.Tool(
        name="memory_list",
        description="List all keys in facts memory.",
        inputSchema={"type": "object", "properties": {}}
    ),
    types.Tool(
        name="memory_search",
        description="Fuzzy search across all memory namespaces. Returns top 15 matches with scores.",
        inputSchema={"type": "object", "required": ["query"], "properties": {
            "query": {"type": "string"}
        }}
    ),
    types.Tool(
        name="memory_compress",
        description=(
            "Compress a long conversation into a compact memory fact via LM Studio. "
            "Use when context grows long to free token space."
        ),
        inputSchema={"type": "object", "required": ["conversation","label"], "properties": {
            "conversation": {"type": "string"},
            "label":        {"type": "string"}
        }}
    ),

    # ======================================================================
    # MEMORY: CHUNKS
    # ======================================================================
    types.Tool(
        name="memory_chunk_save",
        description="Split and store large text as numbered disk chunks (~6000 chars each). Use for content > 4000 chars.",
        inputSchema={"type": "object", "required": ["label","text"], "properties": {
            "label": {"type": "string"},
            "text":  {"type": "string"}
        }}
    ),
    types.Tool(
        name="memory_chunk_load",
        description="Load one chunk by label and index. Check has_more + next_index to iterate.",
        inputSchema={"type": "object", "required": ["label","index"], "properties": {
            "label": {"type": "string"},
            "index": {"type": "integer"}
        }}
    ),
    types.Tool(
        name="memory_chunk_reassemble",
        description="Reassemble all chunks for a label into full text. Only if total_chars < 20000.",
        inputSchema={"type": "object", "required": ["label"], "properties": {
            "label": {"type": "string"}
        }}
    ),
    types.Tool(
        name="memory_chunk_list",
        description="List all stored chunk labels with total_chars and chunk count.",
        inputSchema={"type": "object", "properties": {}}
    ),
    types.Tool(
        name="memory_chunk_delete",
        description="Delete all chunks for a label.",
        inputSchema={"type": "object", "required": ["label"], "properties": {
            "label": {"type": "string"}
        }}
    ),

    # ======================================================================
    # MEMORY: TASKS
    # ======================================================================
    types.Tool(
        name="memory_task_start",
        description="Create a task record for a long or multi-session goal.",
        inputSchema={"type": "object", "required": ["task_id","goal"], "properties": {
            "task_id": {"type": "string"},
            "goal":    {"type": "string"}
        }}
    ),
    types.Tool(
        name="memory_task_update",
        description="Log a step and update task status. Call after each meaningful action.",
        inputSchema={"type": "object", "required": ["task_id","step"], "properties": {
            "task_id": {"type": "string"},
            "step":    {"type": "string"},
            "status":  {"type": "string", "enum": ["in_progress","complete","blocked","failed"], "default": "in_progress"},
            "summary": {"type": "string", "default": ""}
        }}
    ),
    types.Tool(
        name="memory_task_load",
        description="Load a full task record by task_id.",
        inputSchema={"type": "object", "required": ["task_id"], "properties": {
            "task_id": {"type": "string"}
        }}
    ),
    types.Tool(
        name="memory_task_list",
        description="List all tasks with status and step count. Call at session start to find unfinished work.",
        inputSchema={"type": "object", "properties": {}}
    ),

    # ======================================================================
    # MEMORY: CACHE
    # ======================================================================
    types.Tool(
        name="memory_cache_set",
        description="Store ephemeral data in a keyed cache. Auto-evicted at 100 entries.",
        inputSchema={"type": "object", "required": ["key","value"], "properties": {
            "key":   {"type": "string"},
            "value": {"type": "string"},
            "ttl":   {"type": "integer", "default": 0}
        }}
    ),
    types.Tool(
        name="memory_cache_get",
        description="Retrieve a cached value. Returns CACHE MISS if expired or not found.",
        inputSchema={"type": "object", "required": ["key"], "properties": {
            "key": {"type": "string"}
        }}
    ),
    types.Tool(
        name="memory_cache_list",
        description="List all active (non-expired) cache keys.",
        inputSchema={"type": "object", "properties": {}}
    ),

    # ======================================================================
    # CLIPBOARD
    # ======================================================================
    types.Tool(
        name="clipboard_get",
        description="Read the current Windows clipboard contents.",
        inputSchema={"type": "object", "properties": {}}
    ),
    types.Tool(
        name="clipboard_set",
        description="Write text to the Windows clipboard. Use before ctrl+v to paste large text.",
        inputSchema={"type": "object", "required": ["text"], "properties": {
            "text": {"type": "string"}
        }}
    ),

    # ======================================================================
    # GOAL ENGINE
    # ======================================================================
    types.Tool(
        name="goal_status",
        description=(
            "Report goal progress. Call at the end of each work loop iteration. "
            "'in_progress' = keep working. 'complete' = done (auto-notifies user). "
            "'blocked' = need user input — describe in blocker (auto-notifies user)."
        ),
        inputSchema={"type": "object", "required": ["status","summary"], "properties": {
            "status":  {"type": "string", "enum": ["in_progress","complete","blocked"]},
            "summary": {"type": "string"},
            "blocker": {"type": "string", "default": ""}
        }}
    ),
]


# ==========================================================================
# TOOL REGISTRY
# ==========================================================================
@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return TOOLS


# ==========================================================================
# TOOL CALL HANDLER
# ==========================================================================
@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent | types.ImageContent]:
    log.info(f"TOOL CALL: {name} | args={json.dumps(arguments, default=str)[:300]}")
    try:
        result = await _dispatch(name, arguments)
    except PermissionError as e:
        result = {"error": "PERMISSION_DENIED", "message": str(e)}
    except Exception as e:
        log.error(f"Tool {name} raised:\n{traceback.format_exc()}")
        result = {"error": type(e).__name__, "message": str(e)}

    if isinstance(result, types.ImageContent):
        log.info(f"TOOL RESULT [{name}]: <image data>")
        return [result]

    text = result if isinstance(result, str) else json.dumps(result, indent=2, ensure_ascii=False, default=str)
    log.info(f"TOOL RESULT [{name}]: {text[:200]}")
    return [types.TextContent(type="text", text=text)]


async def _dispatch(name: str, args: dict) -> Any:

    # ======================================================================
    # INTERNET ROUTING
    # ======================================================================
    if name == "needs_internet":
        from tools.internet_router import needs_internet
        return needs_internet(args["query"])

    # ======================================================================
    # GOOGLE SEARCH & WEB
    # ======================================================================
    if name == "google_search":
        from tools.web_search import google_search
        return await google_search(
            args["query"],
            num_results=args.get("num_results", 5),
            time_range=args.get("time_range", ""),
            site=args.get("site", ""),
            page=args.get("page", 1),
            language=args.get("language", "en"),
            region=args.get("region", "us"),
        )

    if name == "google_news":
        from tools.web_search import google_news
        return await google_news(args["query"], args.get("num_results", 5))

    if name == "google_scholar":
        from tools.web_search import google_scholar
        return await google_scholar(args["query"], args.get("num_results", 5))

    if name == "google_images":
        from tools.web_search import google_images
        return await google_images(args["query"], args.get("num_results", 5))

    if name == "google_trends":
        from tools.web_search import google_trends
        return await google_trends(args["query"])

    if name == "visit_page":
        from tools.web_search import visit_page
        return await visit_page(args["url"])

    # ======================================================================
    # TRAVEL & COMMERCE
    # ======================================================================
    if name == "google_shopping":
        from tools.web_search import google_shopping
        return await google_shopping(args["query"], args.get("num_results", 5))

    if name == "google_flights":
        from tools.web_search import google_flights
        return await google_flights(
            args["origin"], args["destination"],
            args.get("date", ""), args.get("return_date", "")
        )

    if name == "google_hotels":
        from tools.web_search import google_hotels
        return await google_hotels(args["query"], args.get("num_results", 5))

    if name == "google_translate":
        from tools.web_search import google_translate
        return await google_translate(args["text"], args["to_language"], args.get("from_language", "auto"))

    if name == "google_maps":
        from tools.web_search import google_maps
        return await google_maps(args["query"], args.get("num_results", 5))

    if name == "google_maps_directions":
        from tools.web_search import google_maps_directions
        return await google_maps_directions(args["origin"], args["destination"], args.get("mode", "driving"))

    # ======================================================================
    # FINANCE & INFO
    # ======================================================================
    if name == "google_finance":
        from tools.web_search import google_finance
        return await google_finance(args["query"])

    if name == "google_weather":
        from tools.web_search import google_weather
        return await google_weather(args["location"])

    if name == "google_books":
        from tools.web_search import google_books
        return await google_books(args["query"], args.get("num_results", 5))

    # ======================================================================
    # VISION & OCR (web)
    # ======================================================================
    if name == "google_lens":
        from tools.web_search import google_lens
        return await google_lens(args["image_source"])

    if name == "google_lens_detect":
        from tools.web_search import google_lens_detect
        return await google_lens_detect(args["image_source"])

    if name == "ocr_image":
        from tools.web_search import ocr_image
        return await ocr_image(args["image_source"])

    if name == "list_images":
        from tools.web_search import list_images
        return await list_images(args.get("directory", ""))

    # ======================================================================
    # VIDEO & AUDIO
    # ======================================================================
    if name == "transcribe_video":
        from tools.web_search import transcribe_video
        return await transcribe_video(
            args["url"],
            args.get("model_size", "tiny"),
            args.get("language", ""),
        )

    if name == "transcribe_local":
        from tools.web_search import transcribe_local
        # web_search.py signature: transcribe_local(file_path, model_size, language)
        return await transcribe_local(
            args["file_path"],
            args.get("model_size", "tiny"),
            args.get("language", ""),
        )

    if name == "search_transcript":
        from tools.web_search import search_transcript
        # web_search.py signature: search_transcript(url, keyword)
        return await search_transcript(args["url"], args["keyword"])

    if name == "extract_video_clip":
        from tools.web_search import extract_video_clip
        # web_search.py signature: extract_video_clip(url, description, output_path)
        return await extract_video_clip(
            args["url"],
            args["description"],
            args.get("output_path", ""),
        )

    if name == "convert_media":
        from tools.web_search import convert_media
        return await convert_media(args["input_path"], args["output_path"])

    # ======================================================================
    # DOCUMENTS & DATA
    # ======================================================================
    if name == "read_document":
        from tools.web_search import read_document
        # web_search.py signature: read_document(file_path)
        return await read_document(args["file_path"])

    # ======================================================================
    # EMAIL
    # ======================================================================
    if name == "fetch_emails":
        from tools.web_search import fetch_emails
        # web_search.py signature: fetch_emails(email_address, password, server, port, num_emails, folder)
        return await fetch_emails(
            args["email_address"],
            args["password"],
            server=args.get("server", ""),
            port=args.get("port", 993),
            num_emails=args.get("num_emails", 10),
            folder=args.get("folder", "INBOX"),
        )

    # ======================================================================
    # WEB UTILITIES
    # ======================================================================
    if name == "paste_text":
        from tools.web_search import paste_text
        return await paste_text(args["text"])

    if name == "shorten_url":
        from tools.web_search import shorten_url
        return await shorten_url(args["url"])

    if name == "generate_qr":
        from tools.web_search import generate_qr
        return await generate_qr(args["data"], args.get("output_path", ""))

    if name == "archive_webpage":
        from tools.web_search import archive_webpage
        return await archive_webpage(args["url"])

    if name == "wikipedia":
        from tools.web_search import wikipedia
        return await wikipedia(args["query"], args.get("lang", "en"))

    # ======================================================================
    # CLOUD STORAGE
    # ======================================================================
    if name == "upload_to_s3":
        from tools.web_search import upload_to_s3
        return await upload_to_s3(
            args["file_path"], args["bucket"],
            endpoint_url=args.get("endpoint_url", ""),
            access_key=args.get("access_key", ""),
            secret_key=args.get("secret_key", ""),
            region=args.get("region", "us-east-1"),
        )

    # ======================================================================
    # FEED SUBSCRIPTIONS
    # ======================================================================
    if name == "subscribe":
        from tools.web_search import subscribe
        return await subscribe(args["source_type"], args["identifier"], args.get("name", ""))

    if name == "unsubscribe":
        from tools.web_search import unsubscribe
        return await unsubscribe(args["source_type"], args["identifier"])

    if name == "list_subscriptions":
        from tools.web_search import list_subscriptions
        return await list_subscriptions()

    if name == "check_feeds":
        from tools.web_search import check_feeds
        return await check_feeds(args.get("source_type", ""))

    if name == "search_feeds":
        from tools.web_search import search_feeds
        return await search_feeds(args["query"], args.get("source_type", ""), args.get("limit", 20))

    if name == "get_feed_items":
        from tools.web_search import get_feed_items
        return await get_feed_items(
            source=args.get("source", ""),
            source_type=args.get("source_type", ""),
            limit=args.get("limit", 20),
        )

    # ======================================================================
    # PC VISION (SCREEN)
    # ======================================================================
    if name == "screenshot":
        from tools.pc_vision import take_screenshot
        b64 = await take_screenshot(args.get("region", "full"))
        return types.ImageContent(type="image", data=b64, mimeType="image/jpeg")

    if name == "get_screen_info":
        from tools.pc_vision import get_screen_info
        return get_screen_info()

    if name == "ocr":
        from tools.ocr import ocr_region
        return await ocr_region(args.get("region", "full"), args.get("lang", "eng"))

    # ======================================================================
    # MOUSE
    # ======================================================================
    if name == "mouse_move":
        from tools.mouse_kb import mouse_move
        return await mouse_move(args["x"], args["y"], args.get("duration", 0.15))

    if name == "mouse_click":
        from tools.mouse_kb import mouse_click
        return await mouse_click(args["x"], args["y"], args.get("button", "left"), args.get("clicks", 1))

    if name == "mouse_double_click":
        from tools.mouse_kb import mouse_double_click
        return await mouse_double_click(args["x"], args["y"])

    if name == "mouse_right_click":
        from tools.mouse_kb import mouse_right_click
        return await mouse_right_click(args["x"], args["y"])

    if name == "mouse_scroll":
        from tools.mouse_kb import mouse_scroll
        return await mouse_scroll(args["x"], args["y"], args["clicks"])

    if name == "mouse_drag":
        from tools.mouse_kb import mouse_drag
        return await mouse_drag(
            args["x1"], args["y1"], args["x2"], args["y2"],
            args.get("duration", 0.4), args.get("button", "left")
        )

    # ======================================================================
    # KEYBOARD
    # ======================================================================
    if name == "keyboard_type":
        from tools.mouse_kb import keyboard_type
        return await keyboard_type(args["text"], args.get("interval", 0.02))

    if name == "keyboard_hotkey":
        from tools.mouse_kb import keyboard_hotkey
        return await keyboard_hotkey(args["keys"])

    if name == "keyboard_press":
        from tools.mouse_kb import keyboard_press
        return await keyboard_press(args["key"], args.get("presses", 1))

    if name == "keyboard_key_down":
        from tools.mouse_kb import keyboard_key_down
        return await keyboard_key_down(args["key"])

    if name == "keyboard_key_up":
        from tools.mouse_kb import keyboard_key_up
        return await keyboard_key_up(args["key"])

    # ======================================================================
    # SHELL
    # ======================================================================
    if name == "run_cmd":
        from tools.shell import run_cmd
        return await run_cmd(args["command"], args.get("timeout", 30))

    if name == "run_powershell":
        from tools.shell import run_powershell
        return await run_powershell(args["command"], args.get("timeout", 30))

    if name == "run_python":
        from tools.shell import run_python
        return await run_python(args["code"], args.get("timeout", 30))

    if name == "run_persistent_cmd":
        from tools.shell import run_persistent_cmd
        return await run_persistent_cmd(args["command"], args.get("timeout", 30))

    if name == "reset_persistent_cmd":
        from tools.shell import reset_persistent_cmd
        return await reset_persistent_cmd()

    # ======================================================================
    # FILES
    # ======================================================================
    if name == "read_file":
        from tools.file_ops import read_file
        return await read_file(args["path"])

    if name == "write_file":
        from tools.auth_guard import requires_auth
        from tools.file_ops import write_file
        return await requires_auth(write_file, args["path"], args["content"])

    if name == "append_file":
        from tools.auth_guard import requires_auth
        from tools.file_ops import append_file
        return await requires_auth(append_file, args["path"], args["content"])

    if name == "list_dir":
        from tools.file_ops import list_dir
        return await list_dir(args["path"])

    if name == "delete_file":
        from tools.auth_guard import requires_auth
        from tools.file_ops import delete_file
        return await requires_auth(delete_file, args["path"])

    if name == "file_exists":
        from tools.file_ops import file_exists
        return file_exists(args["path"])

    if name == "search_files":
        from tools.file_ops import search_files
        return await search_files(args["root"], args["pattern"])

    # ======================================================================
    # PROCESSES
    # ======================================================================
    if name == "list_processes":
        from tools.process_ops import list_processes
        return await list_processes(args.get("sort_by", "ram"), args.get("limit", 50))

    if name == "find_process":
        from tools.process_ops import find_process
        return await find_process(args["name"])

    if name == "kill_process":
        from tools.process_ops import kill_process
        return await kill_process(args["pid"], args.get("force", False))

    if name == "launch_app":
        from tools.process_ops import launch_app
        return await launch_app(args["target"], args.get("wait", False), args.get("timeout", 10))

    # ======================================================================
    # WINDOWS
    # ======================================================================
    if name == "list_windows":
        from tools.window_ops import list_windows
        return await list_windows()

    if name == "focus_window":
        from tools.window_ops import focus_window
        return await focus_window(args["title"])

    if name == "get_active_window":
        from tools.window_ops import get_active_window
        return get_active_window()

    if name == "get_window_rect":
        from tools.window_ops import get_window_rect
        return await get_window_rect(args["title"])

    if name == "minimize_window":
        from tools.window_ops import minimize_window
        return await minimize_window(args["title"])

    if name == "maximize_window":
        from tools.window_ops import maximize_window
        return await maximize_window(args["title"])

    if name == "restore_window":
        from tools.window_ops import restore_window
        return await restore_window(args["title"])

    if name == "resize_window":
        from tools.window_ops import resize_window
        return await resize_window(args["title"], args["width"], args["height"])

    if name == "move_window":
        from tools.window_ops import move_window
        return await move_window(args["title"], args["x"], args["y"])

    # ======================================================================
    # PC INFO
    # ======================================================================
    if name == "get_pc_snapshot":
        from tools.pc_info import get_pc_snapshot
        return await get_pc_snapshot()

    # ======================================================================
    # NOTIFY
    # ======================================================================
    if name == "notify_user":
        from tools.notify import notify_user
        return await notify_user(args["title"], args["message"], args.get("duration", 5))

    # ======================================================================
    # MEMORY: FACTS
    # ======================================================================
    if name == "memory_save":     return mem.save(args["key"], args["value"])
    if name == "memory_get":      return mem.get(args["key"])
    if name == "memory_delete":   return mem.delete(args["key"])
    if name == "memory_list":     return mem.list_keys()
    if name == "memory_search":   return mem.search(args["query"])
    if name == "memory_compress": return await mem.compress(args["conversation"], args["label"])

    # ======================================================================
    # MEMORY: CHUNKS
    # ======================================================================
    if name == "memory_chunk_save":       return mem.chunk_save(args["label"], args["text"])
    if name == "memory_chunk_load":       return mem.chunk_load(args["label"], args["index"])
    if name == "memory_chunk_reassemble": return mem.chunk_reassemble(args["label"])
    if name == "memory_chunk_list":       return mem.chunk_list()
    if name == "memory_chunk_delete":     return mem.chunk_delete(args["label"])

    # ======================================================================
    # MEMORY: TASKS
    # ======================================================================
    if name == "memory_task_start":  return mem.task_start(args["task_id"], args["goal"])
    if name == "memory_task_update": return mem.task_update(
        args["task_id"], args["step"],
        args.get("status", "in_progress"), args.get("summary", "")
    )
    if name == "memory_task_load":   return mem.task_load(args["task_id"])
    if name == "memory_task_list":   return mem.task_list()

    # ======================================================================
    # MEMORY: CACHE
    # ======================================================================
    if name == "memory_cache_set":  return mem.cache_set(args["key"], args["value"], args.get("ttl", 0))
    if name == "memory_cache_get":  return mem.cache_get(args["key"])
    if name == "memory_cache_list": return mem.cache_list()

    # ======================================================================
    # CLIPBOARD
    # ======================================================================
    if name == "clipboard_get":
        from tools.clipboard import clipboard_get
        return clipboard_get()

    if name == "clipboard_set":
        from tools.clipboard import clipboard_set
        return clipboard_set(args["text"])

    # ======================================================================
    # GOAL ENGINE
    # ======================================================================
    if name == "goal_status":
        status  = args["status"]
        summary = args["summary"]
        blocker = args.get("blocker", "")
        entry   = {"status": status, "summary": summary}
        if blocker:
            entry["blocker"] = blocker
        mem.save("__last_goal_status", json.dumps(entry))

        if status == "blocked":
            log.warning(f"GOAL BLOCKED: {blocker}")
            try:
                from tools.notify import notify_user
                await notify_user(
                    "Phantom — Blocked",
                    f"{blocker[:200]}" if blocker else summary[:200],
                    duration=10,
                )
            except Exception:
                pass
        elif status == "complete":
            log.info(f"GOAL COMPLETE: {summary}")
            try:
                from tools.notify import notify_user
                await notify_user(
                    "Phantom — Goal Complete ✅",
                    summary[:200],
                    duration=8,
                )
            except Exception:
                pass

        return entry

    # ======================================================================
    # UNKNOWN
    # ======================================================================
    return {"error": f"Unknown tool: '{name}'"}


# ==========================================================================
# ENTRYPOINT
# ==========================================================================
async def main():
    from ui.tray import start_tray_thread
    start_tray_thread()
    log.info("Phantom MCP server starting on stdio transport...")
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
