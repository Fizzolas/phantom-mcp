"""
Phantom MCP Server — Sweep 4

Fix pass: aligns _dispatch calls with actual web_search.py function signatures.
  - extract_video_clip: web_search.py takes (url, description, output_path)
    NOT (url, start_time, end_time). Tool schema corrected to match.
  - fetch_emails: web_search.py takes email_address=, not email=
  - transcribe_local: web_search.py takes file_path=, not path=

Bug-fix sweep (checklist):
  - Logging split: file handler DEBUG, stderr handler WARNING-only so
    normal INFO noise no longer pollutes LM Studio stderr.
  - PATH guard: warns at startup if noapi-google-search-mcp binary is missing.
  - Goal loop: set_goal/_active_goal persists across calls; each call_tool
    increments _consecutive_failures on error and resets on success;
    after MAX_CONSECUTIVE_FAILURES it stalls gracefully instead of looping.
  - read_dir_tree: TOOL definition added + dispatch case added.
  - focus_window: dispatch updated to expect dict result (ok/error).
  - keyboard_type: misaligned indent fixed (was only typing first char).
  - needs_internet: wrapped in asyncio.to_thread so sync HTTP never blocks loop.

All 100+ tools are registered and dispatched.
"""
import asyncio
import json
import logging
import sys
import traceback
import shutil
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

log = logging.getLogger("phantom")
log.setLevel(logging.DEBUG)
log.handlers.clear()

_file_handler = logging.FileHandler(LOG_DIR / "server.log", encoding="utf-8")
_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

_stderr_handler = logging.StreamHandler(sys.stderr)
_stderr_handler.setLevel(logging.WARNING)
_stderr_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

log.addHandler(_file_handler)
log.addHandler(_stderr_handler)
log.propagate = False
print("Phantom MCP server ready.", flush=True)

from memory.manager import MemoryManager
mem = MemoryManager(ROOT / "data")

if shutil.which("noapi-google-search-mcp") is None:
    log.warning("noapi-google-search-mcp not found on PATH; external google-search MCP may fail to start.")

app = Server("phantom-mcp")

_tools_list_logged = False
_active_goal = {"goal": None, "status": "idle", "steps": []}
_consecutive_failures = 0
MAX_CONSECUTIVE_FAILURES = 3

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
            "date":        {"type": "string", "description": "Departure date, YYYY-MM-DD", "default": ""}
        }}
    ),
    types.Tool(
        name="google_hotels",
        description=(
            "Search Google Hotels for accommodation options. "
            "Returns name, price, rating, and availability summary."
        ),
        inputSchema={"type": "object", "required": ["location"], "properties": {
            "location":    {"type": "string"},
            "check_in":    {"type": "string", "description": "Check-in date YYYY-MM-DD", "default": ""},
            "check_out":   {"type": "string", "description": "Check-out date YYYY-MM-DD", "default": ""},
            "num_results": {"type": "integer", "default": 5}
        }}
    ),
    types.Tool(
        name="amazon_search",
        description=(
            "Search Amazon.com for products. Returns title, price, rating, review count, and URL. "
            "Preferred over google_shopping for Amazon-specific product hunting."
        ),
        inputSchema={"type": "object", "required": ["query"], "properties": {
            "query":       {"type": "string"},
            "num_results": {"type": "integer", "default": 5},
            "page":        {"type": "integer", "default": 1}
        }}
    ),
    types.Tool(
        name="ebay_search",
        description="Search eBay listings. Returns title, price, condition, and URL.",
        inputSchema={"type": "object", "required": ["query"], "properties": {
            "query":       {"type": "string"},
            "num_results": {"type": "integer", "default": 5},
            "condition":   {"type": "string", "enum": ["any", "new", "used"], "default": "any"}
        }}
    ),
    types.Tool(
        name="craigslist_search",
        description="Search Craigslist listings for local items or services.",
        inputSchema={"type": "object", "required": ["query", "city"], "properties": {
            "query":    {"type": "string"},
            "city":     {"type": "string", "description": "Craigslist city subdomain, e.g. 'newyork'"},
            "category": {"type": "string", "default": ""}
        }}
    ),
    types.Tool(
        name="youtube_search",
        description="Search YouTube for videos. Returns title, URL, channel, view count, and description snippet.",
        inputSchema={"type": "object", "required": ["query"], "properties": {
            "query":       {"type": "string"},
            "num_results": {"type": "integer", "default": 5}
        }}
    ),
    types.Tool(
        name="download_youtube",
        description="Download a YouTube video or audio track to a local file.",
        inputSchema={"type": "object", "required": ["url", "output_path"], "properties": {
            "url":         {"type": "string"},
            "output_path": {"type": "string"},
            "audio_only":  {"type": "boolean", "default": False}
        }}
    ),
    types.Tool(
        name="extract_video_clip",
        description="Extract a short clip from a video file using FFmpeg. Provide a text description of the portion you want; Phantom will find the right segment.",
        inputSchema={"type": "object", "required": ["url", "description", "output_path"], "properties": {
            "url":         {"type": "string", "description": "Source video URL or local path"},
            "description": {"type": "string", "description": "Text description of the clip content to extract"},
            "output_path": {"type": "string"}
        }}
    ),

    # ======================================================================
    # MAPS, WEATHER, FINANCE
    # ======================================================================
    types.Tool(
        name="google_maps",
        description="Get directions, distances, or place info using Google Maps.",
        inputSchema={"type": "object", "required": ["query"], "properties": {
            "query": {"type": "string", "description": "Place name, address, or 'A to B' directions query"}
        }}
    ),
    types.Tool(
        name="get_weather",
        description="Get current weather and a 3-day forecast for any location.",
        inputSchema={"type": "object", "required": ["location"], "properties": {
            "location": {"type": "string"}
        }}
    ),
    types.Tool(
        name="stock_price",
        description="Get the current stock price and basic info for a ticker symbol.",
        inputSchema={"type": "object", "required": ["symbol"], "properties": {
            "symbol": {"type": "string", "description": "Ticker symbol, e.g. AAPL"}
        }}
    ),
    types.Tool(
        name="crypto_price",
        description="Get the current price and 24h change for a cryptocurrency.",
        inputSchema={"type": "object", "required": ["symbol"], "properties": {
            "symbol": {"type": "string", "description": "Coin symbol, e.g. BTC, ETH"}
        }}
    ),
    types.Tool(
        name="currency_convert",
        description="Convert an amount from one currency to another using live exchange rates.",
        inputSchema={"type": "object", "required": ["amount", "from_currency", "to_currency"], "properties": {
            "amount":        {"type": "number"},
            "from_currency": {"type": "string"},
            "to_currency":   {"type": "string"}
        }}
    ),
    types.Tool(
        name="translate_text",
        description="Translate text to a target language using Google Translate.",
        inputSchema={"type": "object", "required": ["text", "target_language"], "properties": {
            "text":            {"type": "string"},
            "target_language": {"type": "string", "description": "BCP-47 language code, e.g. 'es', 'fr', 'ja'"}
        }}
    ),

    # ======================================================================
    # EMAIL & CALENDAR
    # ======================================================================
    types.Tool(
        name="fetch_emails",
        description=(
            "Fetch recent emails from a Gmail account using IMAP. "
            "Returns subject, sender, date, and body snippet for each message."
        ),
        inputSchema={"type": "object", "required": ["email_address"], "properties": {
            "email_address": {"type": "string"},
            "num_emails":    {"type": "integer", "default": 10},
            "folder":        {"type": "string", "default": "INBOX"}
        }}
    ),
    types.Tool(
        name="send_email",
        description="Send an email via SMTP.",
        inputSchema={"type": "object", "required": ["to", "subject", "body"], "properties": {
            "to":      {"type": "string"},
            "subject": {"type": "string"},
            "body":    {"type": "string"},
            "cc":      {"type": "string", "default": ""},
            "bcc":     {"type": "string", "default": ""}
        }}
    ),
    types.Tool(
        name="calendar_events",
        description="Fetch upcoming calendar events from a Google Calendar.",
        inputSchema={"type": "object", "required": [], "properties": {
            "days_ahead": {"type": "integer", "default": 7}
        }}
    ),
    types.Tool(
        name="create_calendar_event",
        description="Create a new Google Calendar event.",
        inputSchema={"type": "object", "required": ["title", "start_time", "end_time"], "properties": {
            "title":       {"type": "string"},
            "start_time":  {"type": "string", "description": "ISO 8601 datetime"},
            "end_time":    {"type": "string"},
            "description": {"type": "string", "default": ""},
            "location":    {"type": "string", "default": ""}
        }}
    ),

    # ======================================================================
    # SOCIAL MEDIA
    # ======================================================================
    types.Tool(
        name="twitter_search",
        description="Search Twitter/X for recent tweets on a topic.",
        inputSchema={"type": "object", "required": ["query"], "properties": {
            "query":       {"type": "string"},
            "num_results": {"type": "integer", "default": 10}
        }}
    ),
    types.Tool(
        name="reddit_search",
        description="Search Reddit posts and comments. Returns title, subreddit, score, and URL.",
        inputSchema={"type": "object", "required": ["query"], "properties": {
            "query":       {"type": "string"},
            "num_results": {"type": "integer", "default": 5},
            "subreddit":   {"type": "string", "default": ""}
        }}
    ),
    types.Tool(
        name="linkedin_search",
        description="Search LinkedIn for people, jobs, or companies.",
        inputSchema={"type": "object", "required": ["query"], "properties": {
            "query":       {"type": "string"},
            "search_type": {"type": "string", "enum": ["people", "jobs", "companies"], "default": "people"}
        }}
    ),

    # ======================================================================
    # SYSTEM & SHELL
    # ======================================================================
    types.Tool(
        name="run_cmd",
        description=(
            "Run a Windows CMD command and return stdout/stderr. "
            "Use for simple one-liners: dir, ping, ipconfig, etc. "
            "For multi-step scripts, prefer run_powershell."
        ),
        inputSchema={"type": "object", "required": ["command"], "properties": {
            "command": {"type": "string"},
            "timeout": {"type": "integer", "default": 30, "description": "Max seconds to wait"}
        }}
    ),
    types.Tool(
        name="run_powershell",
        description=(
            "Run a PowerShell script or command block and return stdout/stderr. "
            "Preferred for multi-step logic, file operations, registry edits, "
            "service management, and anything that needs .NET APIs. "
            "Supports multi-line scripts."
        ),
        inputSchema={"type": "object", "required": ["script"], "properties": {
            "script":  {"type": "string"},
            "timeout": {"type": "integer", "default": 60}
        }}
    ),
    types.Tool(
        name="run_python",
        description=(
            "Execute a Python script and return its stdout. "
            "Use for data processing, parsing, calculations, or automation tasks "
            "that are easier in Python than PowerShell."
        ),
        inputSchema={"type": "object", "required": ["code"], "properties": {
            "code":    {"type": "string"},
            "timeout": {"type": "integer", "default": 60}
        }}
    ),
    types.Tool(
        name="process_list",
        description="List running processes with PID, name, CPU%, and memory usage.",
        inputSchema={"type": "object", "required": [], "properties": {
            "filter": {"type": "string", "description": "Optional substring filter on process name", "default": ""}
        }}
    ),
    types.Tool(
        name="kill_process",
        description="Kill a running process by PID or name.",
        inputSchema={"type": "object", "required": ["target"], "properties": {
            "target": {"type": ["integer", "string"], "description": "PID (integer) or process name (string)"}
        }}
    ),
    types.Tool(
        name="system_info",
        description=(
            "Get a full snapshot of system hardware and OS: CPU model, core count, RAM, "
            "disk usage, GPU name, OS version, uptime, and current user. "
            "Call once at session start to understand what machine you are running on."
        ),
        inputSchema={"type": "object", "required": [], "properties": {}}
    ),
    types.Tool(
        name="get_env",
        description="Read one or all environment variables.",
        inputSchema={"type": "object", "required": [], "properties": {
            "key": {"type": "string", "description": "Variable name. Omit to list all.", "default": ""}
        }}
    ),
    types.Tool(
        name="set_env",
        description="Set an environment variable for the current session.",
        inputSchema={"type": "object", "required": ["key", "value"], "properties": {
            "key":   {"type": "string"},
            "value": {"type": "string"}
        }}
    ),
    types.Tool(
        name="get_clipboard",
        description="Read the current contents of the Windows clipboard.",
        inputSchema={"type": "object", "required": [], "properties": {}}
    ),
    types.Tool(
        name="set_clipboard",
        description="Write text to the Windows clipboard.",
        inputSchema={"type": "object", "required": ["text"], "properties": {
            "text": {"type": "string"}
        }}
    ),

    # ======================================================================
    # FILE SYSTEM
    # ======================================================================
    types.Tool(
        name="read_file",
        description=(
            "Read the text contents of a file. Caps output at 12 000 chars. "
            "For reading whole folders in one call, use read_dir_tree instead. "
            "For large documents (PDF, DOCX), use read_document."
        ),
        inputSchema={"type": "object", "required": ["path"], "properties": {
            "path": {"type": "string"}
        }}
    ),
    types.Tool(
        name="read_dir_tree",
        description=(
            "List all entries in a directory AND return the text contents of up to "
            "`max_files` matching files in a single call. "
            "Use instead of list_dir + multiple read_file calls when you need to "
            "understand an entire folder structure at once. "
            "Prefer this over chained read_file, read_document, or read_dir_tree calls "
            "whenever the goal requires reviewing multiple files."
        ),
        inputSchema={"type": "object", "required": ["root"], "properties": {
            "root":      {"type": "string", "description": "Absolute or relative path to the folder"},
            "pattern":   {"type": "string", "default": "**/*", "description": "Glob pattern, e.g. '**/*.py'"},
            "max_files": {"type": "integer", "default": 10, "description": "Max files to read contents for"}
        }}
    ),
    types.Tool(
        name="write_file",
        description="Write (overwrite) a text file at the given path. Creates parent directories if needed.",
        inputSchema={"type": "object", "required": ["path", "content"], "properties": {
            "path":    {"type": "string"},
            "content": {"type": "string"}
        }}
    ),
    types.Tool(
        name="append_file",
        description="Append text to a file without overwriting existing content.",
        inputSchema={"type": "object", "required": ["path", "content"], "properties": {
            "path":    {"type": "string"},
            "content": {"type": "string"}
        }}
    ),
    types.Tool(
        name="delete_file",
        description="Delete a file or directory. Requires auth_guard if the file was not created by this agent.",
        inputSchema={"type": "object", "required": ["path"], "properties": {
            "path": {"type": "string"}
        }}
    ),
    types.Tool(
        name="list_dir",
        description=(
            "List the contents of a directory (one level). "
            "Returns name, type (file/dir), and size for each entry. "
            "Use read_dir_tree for recursive listings or reading file contents."
        ),
        inputSchema={"type": "object", "required": ["path"], "properties": {
            "path": {"type": "string"}
        }}
    ),
    types.Tool(
        name="search_files",
        description="Recursively search for files matching a glob pattern under a root directory.",
        inputSchema={"type": "object", "required": ["root", "pattern"], "properties": {
            "root":    {"type": "string"},
            "pattern": {"type": "string", "description": "Glob pattern, e.g. '*.log', '*.py'"}
        }}
    ),
    types.Tool(
        name="file_exists",
        description="Check whether a file or directory exists at a given path.",
        inputSchema={"type": "object", "required": ["path"], "properties": {
            "path": {"type": "string"}
        }}
    ),
    types.Tool(
        name="read_document",
        description=(
            "Extract text from a PDF, DOCX, XLSX, PPTX, or image file. "
            "Use for user-created documents that read_file cannot parse."
        ),
        inputSchema={"type": "object", "required": ["path"], "properties": {
            "path": {"type": "string"}
        }}
    ),

    # ======================================================================
    # WINDOW MANAGEMENT
    # ======================================================================
    types.Tool(
        name="list_windows",
        description=(
            "List all visible window titles along with position, size, and active state. "
            "Call before focus_window or get_window_rect if you are unsure of the exact title."
        ),
        inputSchema={"type": "object", "required": [], "properties": {}}
    ),
    types.Tool(
        name="focus_window",
        description=(
            "Bring a window to the foreground by title (case-insensitive substring match by default). "
            "Returns a dict: {ok, focused, method} on success or {ok, error, available_titles} on failure. "
            "Set strict=True for exact-match mode. "
            "Always check ok==True before proceeding with mouse/keyboard actions."
        ),
        inputSchema={"type": "object", "required": ["title"], "properties": {
            "title":  {"type": "string"},
            "strict": {"type": "boolean", "default": False}
        }}
    ),
    types.Tool(
        name="get_active_window",
        description="Return the title, position, and size of the currently focused window.",
        inputSchema={"type": "object", "required": [], "properties": {}}
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
        description="Restore a minimized or maximized window to its normal size.",
        inputSchema={"type": "object", "required": ["title"], "properties": {
            "title": {"type": "string"}
        }}
    ),
    types.Tool(
        name="get_window_rect",
        description="Get exact position and size of a window by title. Returns left, top, width, height, center_x, center_y.",
        inputSchema={"type": "object", "required": ["title"], "properties": {
            "title": {"type": "string"}
        }}
    ),
    types.Tool(
        name="resize_window",
        description="Resize a window to specific pixel dimensions.",
        inputSchema={"type": "object", "required": ["title", "width", "height"], "properties": {
            "title":  {"type": "string"},
            "width":  {"type": "integer"},
            "height": {"type": "integer"}
        }}
    ),
    types.Tool(
        name="move_window",
        description="Move a window to specific screen coordinates.",
        inputSchema={"type": "object", "required": ["title", "x", "y"], "properties": {
            "title": {"type": "string"},
            "x":     {"type": "integer"},
            "y":     {"type": "integer"}
        }}
    ),

    # ======================================================================
    # MOUSE & KEYBOARD
    # ======================================================================
    types.Tool(
        name="mouse_click",
        description=(
            "Move the mouse to (x, y) and click. "
            "button: 'left' (default), 'right', 'middle'. "
            "double: True for double-click. "
            "Always call focus_window first so the click lands on the right app."
        ),
        inputSchema={"type": "object", "required": ["x", "y"], "properties": {
            "x":      {"type": "integer"},
            "y":      {"type": "integer"},
            "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
            "double": {"type": "boolean", "default": False}
        }}
    ),
    types.Tool(
        name="mouse_move",
        description="Move the mouse cursor to (x, y) without clicking.",
        inputSchema={"type": "object", "required": ["x", "y"], "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"}
        }}
    ),
    types.Tool(
        name="mouse_scroll",
        description="Scroll the mouse wheel at the current cursor position.",
        inputSchema={"type": "object", "required": ["clicks"], "properties": {
            "clicks": {"type": "integer", "description": "Positive = scroll up, negative = scroll down"}
        }}
    ),
    types.Tool(
        name="mouse_drag",
        description="Click and drag from (x1, y1) to (x2, y2).",
        inputSchema={"type": "object", "required": ["x1", "y1", "x2", "y2"], "properties": {
            "x1":     {"type": "integer"},
            "y1":     {"type": "integer"},
            "x2":     {"type": "integer"},
            "y2":     {"type": "integer"},
            "button": {"type": "string", "default": "left"}
        }}
    ),
    types.Tool(
        name="keyboard_type",
        description=(
            "Type a string of text as keyboard input at the current cursor position. "
            "For special keys (Enter, Tab, Esc, Ctrl+C etc.), use keyboard_hotkey instead."
        ),
        inputSchema={"type": "object", "required": ["text"], "properties": {
            "text":     {"type": "string"},
            "interval": {"type": "number", "default": 0.02, "description": "Seconds between keystrokes"}
        }}
    ),
    types.Tool(
        name="keyboard_hotkey",
        description=(
            "Press a keyboard shortcut or special key combination. "
            "Examples: 'ctrl+c', 'alt+f4', 'win+d', 'enter', 'tab', 'esc', 'f5'. "
            "For typing text, use keyboard_type instead."
        ),
        inputSchema={"type": "object", "required": ["keys"], "properties": {
            "keys": {"type": "string", "description": "Key combo string, e.g. 'ctrl+c'"}
        }}
    ),
    types.Tool(
        name="take_screenshot",
        description=(
            "Take a screenshot of the entire screen or a region and save it to a file. "
            "Returns the file path and dimensions. "
            "Use to verify what is visible on screen before or after GUI actions."
        ),
        inputSchema={"type": "object", "required": ["output_path"], "properties": {
            "output_path": {"type": "string"},
            "region": {
                "type": "object",
                "description": "Optional crop region {left, top, width, height}",
                "properties": {
                    "left":   {"type": "integer"},
                    "top":    {"type": "integer"},
                    "width":  {"type": "integer"},
                    "height": {"type": "integer"}
                }
            }
        }}
    ),
    types.Tool(
        name="find_on_screen",
        description=(
            "Search the screen for a UI element matching a text label or image template. "
            "Returns (x, y) coordinates of the center of the best match. "
            "Use to locate buttons, input fields, or icons before clicking."
        ),
        inputSchema={"type": "object", "required": ["target"], "properties": {
            "target":     {"type": "string", "description": "Text label or description of the element to find"},
            "confidence": {"type": "number", "default": 0.8}
        }}
    ),
    types.Tool(
        name="ocr_screen",
        description="Run OCR on the screen (or a region) and return all visible text with bounding boxes.",
        inputSchema={"type": "object", "required": [], "properties": {
            "region": {
                "type": "object",
                "properties": {
                    "left":   {"type": "integer"},
                    "top":    {"type": "integer"},
                    "width":  {"type": "integer"},
                    "height": {"type": "integer"}
                }
            }
        }}
    ),

    # ======================================================================
    # AUDIO, MEDIA & NOTIFICATIONS
    # ======================================================================
    types.Tool(
        name="text_to_speech",
        description="Convert text to speech and play it aloud on the system speakers.",
        inputSchema={"type": "object", "required": ["text"], "properties": {
            "text":  {"type": "string"},
            "voice": {"type": "string", "default": "", "description": "Optional voice name"}
        }}
    ),
    types.Tool(
        name="play_audio",
        description="Play an audio file (WAV, MP3, OGG).",
        inputSchema={"type": "object", "required": ["path"], "properties": {
            "path": {"type": "string"}
        }}
    ),
    types.Tool(
        name="record_audio",
        description="Record audio from the microphone for a given duration and save to a file.",
        inputSchema={"type": "object", "required": ["output_path", "duration"], "properties": {
            "output_path": {"type": "string"},
            "duration":    {"type": "integer", "description": "Recording duration in seconds"}
        }}
    ),
    types.Tool(
        name="transcribe_local",
        description="Transcribe a local audio or video file to text using Whisper.",
        inputSchema={"type": "object", "required": ["file_path"], "properties": {
            "file_path": {"type": "string"}
        }}
    ),
    types.Tool(
        name="show_notification",
        description="Show a Windows toast notification in the system tray.",
        inputSchema={"type": "object", "required": ["title", "message"], "properties": {
            "title":    {"type": "string"},
            "message":  {"type": "string"},
            "duration": {"type": "integer", "default": 5}
        }}
    ),

    # ======================================================================
    # MEMORY
    # ======================================================================
    types.Tool(
        name="memory_store",
        description=(
            "Store a piece of information in persistent memory with a key. "
            "Use to remember facts, preferences, partial results, or conversation context "
            "that should survive across sessions."
        ),
        inputSchema={"type": "object", "required": ["key", "value"], "properties": {
            "key":   {"type": "string"},
            "value": {"type": "string"}
        }}
    ),
    types.Tool(
        name="memory_retrieve",
        description=(
            "Retrieve a stored memory by key, or search across all memories by keyword. "
            "Returns the stored value or a list of matching entries."
        ),
        inputSchema={"type": "object", "required": [], "properties": {
            "key":    {"type": "string", "description": "Exact key to look up", "default": ""},
            "search": {"type": "string", "description": "Keyword to search across all memories", "default": ""}
        }}
    ),
    types.Tool(
        name="memory_delete",
        description="Delete a stored memory by key.",
        inputSchema={"type": "object", "required": ["key"], "properties": {
            "key": {"type": "string"}
        }}
    ),
    types.Tool(
        name="memory_list",
        description="List all stored memory keys with a short preview of each value.",
        inputSchema={"type": "object", "required": [], "properties": {}}
    ),

    # ======================================================================
    # GOAL MANAGEMENT
    # ======================================================================
    types.Tool(
        name="set_goal",
        description=(
            "Set the current high-level goal Phantom is working toward. "
            "Call this at the start of any multi-step task so Phantom tracks progress. "
            "The goal persists in memory until replaced or marked complete. "
            "Status values: 'active', 'complete', 'blocked'."
        ),
        inputSchema={"type": "object", "required": ["goal"], "properties": {
            "goal":   {"type": "string"},
            "status": {"type": "string", "enum": ["active", "complete", "blocked"], "default": "active"}
        }}
    ),
    types.Tool(
        name="goal_status",
        description=(
            "Report the status of the current goal: what has been done, what remains, "
            "and whether the goal is complete or blocked. "
            "Call this at the end of every tool chain to decide the next step. "
            "Never stop working until goal_status reports 'complete'."
        ),
        inputSchema={"type": "object", "required": ["summary", "status"], "properties": {
            "summary":    {"type": "string", "description": "What has been accomplished so far"},
            "remaining":  {"type": "string", "description": "What still needs to be done", "default": ""},
            "status":     {"type": "string", "enum": ["active", "complete", "blocked"]},
            "next_action": {"type": "string", "description": "The very next tool call or action to take", "default": ""}
        }}
    ),
    types.Tool(
        name="auth_guard",
        description=(
            "Request explicit user authorization before modifying a file or resource "
            "that was NOT created by this agent. "
            "Always call auth_guard before writing or deleting user files, "
            "system config, registry entries, or installed programs."
        ),
        inputSchema={"type": "object", "required": ["action", "target", "reason"], "properties": {
            "action": {"type": "string", "description": "What will be done (e.g. 'delete', 'overwrite')"},
            "target": {"type": "string", "description": "Full path or resource identifier"},
            "reason": {"type": "string", "description": "Why this change is needed to complete the goal"}
        }}
    ),

    # ======================================================================
    # CODE & DEVELOPMENT
    # ======================================================================
    types.Tool(
        name="git_command",
        description="Run a git command in a given directory. Returns stdout/stderr.",
        inputSchema={"type": "object", "required": ["repo_path", "command"], "properties": {
            "repo_path": {"type": "string"},
            "command":   {"type": "string", "description": "Git sub-command, e.g. 'status', 'log --oneline -10'"}
        }}
    ),
    types.Tool(
        name="pip_install",
        description="Install one or more Python packages via pip.",
        inputSchema={"type": "object", "required": ["packages"], "properties": {
            "packages": {"type": "array", "items": {"type": "string"}}
        }}
    ),
    types.Tool(
        name="npm_command",
        description="Run an npm command in a given directory.",
        inputSchema={"type": "object", "required": ["project_path", "command"], "properties": {
            "project_path": {"type": "string"},
            "command":      {"type": "string"}
        }}
    ),
    types.Tool(
        name="start_dev_server",
        description="Start a development server (npm run dev, python -m http.server, etc.) in the background.",
        inputSchema={"type": "object", "required": ["project_path", "command"], "properties": {
            "project_path": {"type": "string"},
            "command":      {"type": "string"},
            "port":         {"type": "integer", "default": 3000}
        }}
    ),

    # ======================================================================
    # IMAGE / VISION
    # ======================================================================
    types.Tool(
        name="analyze_image",
        description="Analyze an image file and return a detailed text description of its contents.",
        inputSchema={"type": "object", "required": ["path"], "properties": {
            "path":  {"type": "string"},
            "query": {"type": "string", "description": "Optional specific question about the image", "default": ""}
        }}
    ),
    types.Tool(
        name="generate_image",
        description="Generate an image from a text prompt and save it to a file.",
        inputSchema={"type": "object", "required": ["prompt", "output_path"], "properties": {
            "prompt":      {"type": "string"},
            "output_path": {"type": "string"},
            "width":       {"type": "integer", "default": 1024},
            "height":      {"type": "integer", "default": 1024}
        }}
    ),
    types.Tool(
        name="convert_image",
        description="Convert an image from one format to another (PNG, JPEG, WEBP, BMP, GIF).",
        inputSchema={"type": "object", "required": ["input_path", "output_path"], "properties": {
            "input_path":  {"type": "string"},
            "output_path": {"type": "string"}
        }}
    ),
    types.Tool(
        name="resize_image",
        description="Resize an image to specific dimensions.",
        inputSchema={"type": "object", "required": ["input_path", "output_path", "width", "height"], "properties": {
            "input_path":  {"type": "string"},
            "output_path": {"type": "string"},
            "width":       {"type": "integer"},
            "height":      {"type": "integer"}
        }}
    ),

    # ======================================================================
    # BROWSER AUTOMATION
    # ======================================================================
    types.Tool(
        name="browser_open",
        description="Open a URL in a controlled headless/headed browser session.",
        inputSchema={"type": "object", "required": ["url"], "properties": {
            "url":      {"type": "string"},
            "headless": {"type": "boolean", "default": True}
        }}
    ),
    types.Tool(
        name="browser_click",
        description="Click a web element by CSS selector or XPath in the active browser session.",
        inputSchema={"type": "object", "required": ["selector"], "properties": {
            "selector":      {"type": "string"},
            "selector_type": {"type": "string", "enum": ["css", "xpath"], "default": "css"}
        }}
    ),
    types.Tool(
        name="browser_type",
        description="Type text into an input element in the active browser session.",
        inputSchema={"type": "object", "required": ["selector", "text"], "properties": {
            "selector":      {"type": "string"},
            "text":          {"type": "string"},
            "selector_type": {"type": "string", "default": "css"}
        }}
    ),
    types.Tool(
        name="browser_get_html",
        description="Get the full HTML source of the current page in the active browser session.",
        inputSchema={"type": "object", "required": [], "properties": {}}
    ),
    types.Tool(
        name="browser_screenshot",
        description="Take a screenshot of the current browser page and save it to a file.",
        inputSchema={"type": "object", "required": ["output_path"], "properties": {
            "output_path": {"type": "string"}
        }}
    ),
    types.Tool(
        name="browser_execute_js",
        description="Execute JavaScript in the active browser session and return the result.",
        inputSchema={"type": "object", "required": ["script"], "properties": {
            "script": {"type": "string"}
        }}
    ),

    # ======================================================================
    # DATABASE
    # ======================================================================
    types.Tool(
        name="db_query",
        description="Execute a SQL query against a local SQLite database file.",
        inputSchema={"type": "object", "required": ["db_path", "query"], "properties": {
            "db_path": {"type": "string"},
            "query":   {"type": "string"}
        }}
    ),
    types.Tool(
        name="db_execute",
        description="Execute a SQL statement (INSERT/UPDATE/DELETE/CREATE) against a local SQLite database.",
        inputSchema={"type": "object", "required": ["db_path", "statement"], "properties": {
            "db_path":    {"type": "string"},
            "statement":  {"type": "string"},
            "parameters": {"type": "array", "default": []}
        }}
    ),

    # ======================================================================
    # NETWORKING & API
    # ======================================================================
    types.Tool(
        name="http_get",
        description="Perform an HTTP GET request and return status, headers, and body.",
        inputSchema={"type": "object", "required": ["url"], "properties": {
            "url":     {"type": "string"},
            "headers": {"type": "object", "default": {}}
        }}
    ),
    types.Tool(
        name="http_post",
        description="Perform an HTTP POST request with a JSON or form body.",
        inputSchema={"type": "object", "required": ["url"], "properties": {
            "url":      {"type": "string"},
            "body":     {"type": "object", "default": {}},
            "headers":  {"type": "object", "default": {}},
            "form":     {"type": "boolean", "default": False}
        }}
    ),
    types.Tool(
        name="ping_host",
        description="Ping a hostname or IP address and return latency and packet loss.",
        inputSchema={"type": "object", "required": ["host"], "properties": {
            "host":  {"type": "string"},
            "count": {"type": "integer", "default": 4}
        }}
    ),
    types.Tool(
        name="get_public_ip",
        description="Get the machine's current public IP address and geolocation.",
        inputSchema={"type": "object", "required": [], "properties": {}}
    ),
    types.Tool(
        name="port_scan",
        description="Scan a host for open TCP ports in a given range.",
        inputSchema={"type": "object", "required": ["host"], "properties": {
            "host":       {"type": "string"},
            "start_port": {"type": "integer", "default": 1},
            "end_port":   {"type": "integer", "default": 1024}
        }}
    ),

    # ======================================================================
    # GITHUB
    # ======================================================================
    types.Tool(
        name="github_search",
        description="Search GitHub repositories, code, issues, or users.",
        inputSchema={"type": "object", "required": ["query"], "properties": {
            "query":       {"type": "string"},
            "search_type": {"type": "string", "enum": ["repositories", "code", "issues", "users"], "default": "repositories"}
        }}
    ),
    types.Tool(
        name="github_get_file",
        description="Fetch the raw content of a file from a public GitHub repository.",
        inputSchema={"type": "object", "required": ["owner", "repo", "path"], "properties": {
            "owner":  {"type": "string"},
            "repo":   {"type": "string"},
            "path":   {"type": "string"},
            "branch": {"type": "string", "default": "main"}
        }}
    ),
    types.Tool(
        name="github_create_issue",
        description="Create a new issue on a GitHub repository.",
        inputSchema={"type": "object", "required": ["owner", "repo", "title"], "properties": {
            "owner": {"type": "string"},
            "repo":  {"type": "string"},
            "title": {"type": "string"},
            "body":  {"type": "string", "default": ""}
        }}
    ),
]

# ==========================================================================
# TOOL REGISTRY HELPERS
# ==========================================================================

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    global _tools_list_logged
    if not _tools_list_logged:
        log.info("list_tools called — %d tools registered", len(TOOLS))
        _tools_list_logged = True
    return TOOLS


# ==========================================================================
# DISPATCH
# ==========================================================================

async def _dispatch(name: str, args: dict) -> Any:
    """
    Route a tool call to its implementation.
    Returns a JSON-serialisable value.
    All filesystem and shell operations are in sub-modules under tools/.
    """
    # ---- internet routing ----
    if name == "needs_internet":
        from tools.web_search import needs_internet
        return await asyncio.to_thread(needs_internet, args["query"])

    # ---- web / search ----
    if name == "google_search":
        from tools.web_search import google_search
        return await asyncio.to_thread(
            google_search,
            args["query"],
            args.get("num_results", 5),
            args.get("time_range", ""),
            args.get("site", ""),
            args.get("page", 1),
            args.get("language", "en"),
            args.get("region", "us"),
        )
    if name == "google_news":
        from tools.web_search import google_news
        return await asyncio.to_thread(google_news, args["query"], args.get("num_results", 5))
    if name == "google_scholar":
        from tools.web_search import google_scholar
        return await asyncio.to_thread(google_scholar, args["query"], args.get("num_results", 5))
    if name == "google_images":
        from tools.web_search import google_images
        return await asyncio.to_thread(google_images, args["query"], args.get("num_results", 5))
    if name == "google_trends":
        from tools.web_search import google_trends
        return await asyncio.to_thread(google_trends, args["query"])
    if name == "visit_page":
        from tools.web_search import visit_page
        return await asyncio.to_thread(visit_page, args["url"])

    # ---- travel & commerce ----
    if name == "google_shopping":
        from tools.web_search import google_shopping
        return await asyncio.to_thread(google_shopping, args["query"], args.get("num_results", 5))
    if name == "google_flights":
        from tools.web_search import google_flights
        return await asyncio.to_thread(google_flights, args["origin"], args["destination"], args.get("date", ""))
    if name == "google_hotels":
        from tools.web_search import google_hotels
        return await asyncio.to_thread(google_hotels, args["location"], args.get("check_in", ""), args.get("check_out", ""), args.get("num_results", 5))
    if name == "amazon_search":
        from tools.web_search import amazon_search
        return await asyncio.to_thread(amazon_search, args["query"], args.get("num_results", 5), args.get("page", 1))
    if name == "ebay_search":
        from tools.web_search import ebay_search
        return await asyncio.to_thread(ebay_search, args["query"], args.get("num_results", 5), args.get("condition", "any"))
    if name == "craigslist_search":
        from tools.web_search import craigslist_search
        return await asyncio.to_thread(craigslist_search, args["query"], args["city"], args.get("category", ""))
    if name == "youtube_search":
        from tools.web_search import youtube_search
        return await asyncio.to_thread(youtube_search, args["query"], args.get("num_results", 5))
    if name == "download_youtube":
        from tools.web_search import download_youtube
        return await asyncio.to_thread(download_youtube, args["url"], args["output_path"], args.get("audio_only", False))
    if name == "extract_video_clip":
        from tools.web_search import extract_video_clip
        return await asyncio.to_thread(extract_video_clip, args["url"], args["description"], args["output_path"])

    # ---- maps / weather / finance ----
    if name == "google_maps":
        from tools.web_search import google_maps
        return await asyncio.to_thread(google_maps, args["query"])
    if name == "get_weather":
        from tools.web_search import get_weather
        return await asyncio.to_thread(get_weather, args["location"])
    if name == "stock_price":
        from tools.web_search import stock_price
        return await asyncio.to_thread(stock_price, args["symbol"])
    if name == "crypto_price":
        from tools.web_search import crypto_price
        return await asyncio.to_thread(crypto_price, args["symbol"])
    if name == "currency_convert":
        from tools.web_search import currency_convert
        return await asyncio.to_thread(currency_convert, args["amount"], args["from_currency"], args["to_currency"])
    if name == "translate_text":
        from tools.web_search import translate_text
        return await asyncio.to_thread(translate_text, args["text"], args["target_language"])

    # ---- email & calendar ----
    if name == "fetch_emails":
        from tools.web_search import fetch_emails
        return await asyncio.to_thread(
            fetch_emails,
            email_address=args["email_address"],
            num_emails=args.get("num_emails", 10),
            folder=args.get("folder", "INBOX"),
        )
    if name == "send_email":
        from tools.web_search import send_email
        return await asyncio.to_thread(send_email, args["to"], args["subject"], args["body"], args.get("cc", ""), args.get("bcc", ""))
    if name == "calendar_events":
        from tools.web_search import calendar_events
        return await asyncio.to_thread(calendar_events, args.get("days_ahead", 7))
    if name == "create_calendar_event":
        from tools.web_search import create_calendar_event
        return await asyncio.to_thread(create_calendar_event, args["title"], args["start_time"], args["end_time"], args.get("description", ""), args.get("location", ""))

    # ---- social ----
    if name == "twitter_search":
        from tools.web_search import twitter_search
        return await asyncio.to_thread(twitter_search, args["query"], args.get("num_results", 10))
    if name == "reddit_search":
        from tools.web_search import reddit_search
        return await asyncio.to_thread(reddit_search, args["query"], args.get("num_results", 5), args.get("subreddit", ""))
    if name == "linkedin_search":
        from tools.web_search import linkedin_search
        return await asyncio.to_thread(linkedin_search, args["query"], args.get("search_type", "people"))

    # ---- system & shell ----
    if name == "run_cmd":
        from tools.system_ops import run_cmd
        return await run_cmd(args["command"], args.get("timeout", 30))
    if name == "run_powershell":
        from tools.system_ops import run_powershell
        return await run_powershell(args["script"], args.get("timeout", 60))
    if name == "run_python":
        from tools.system_ops import run_python
        return await run_python(args["code"], args.get("timeout", 60))
    if name == "process_list":
        from tools.system_ops import process_list
        return await process_list(args.get("filter", ""))
    if name == "kill_process":
        from tools.system_ops import kill_process
        return await kill_process(args["target"])
    if name == "system_info":
        from tools.system_ops import system_info
        return await asyncio.to_thread(system_info)
    if name == "get_env":
        from tools.system_ops import get_env
        return get_env(args.get("key", ""))
    if name == "set_env":
        from tools.system_ops import set_env
        return set_env(args["key"], args["value"])
    if name == "get_clipboard":
        from tools.system_ops import get_clipboard
        return get_clipboard()
    if name == "set_clipboard":
        from tools.system_ops import set_clipboard
        return set_clipboard(args["text"])

    # ---- file system ----
    if name == "read_file":
        from tools.file_ops import read_file
        return await read_file(args["path"])
    if name == "read_dir_tree":
        from tools.file_ops import read_dir_tree
        return await asyncio.to_thread(
            read_dir_tree,
            args["root"],
            args.get("pattern", "**/*"),
            args.get("max_files", 10),
        )
    if name == "write_file":
        from tools.file_ops import write_file
        return await write_file(args["path"], args["content"])
    if name == "append_file":
        from tools.file_ops import append_file
        return await append_file(args["path"], args["content"])
    if name == "delete_file":
        from tools.file_ops import delete_file
        return await delete_file(args["path"])
    if name == "list_dir":
        from tools.file_ops import list_dir
        return await list_dir(args["path"])
    if name == "search_files":
        from tools.file_ops import search_files
        return await search_files(args["root"], args["pattern"])
    if name == "file_exists":
        from tools.file_ops import file_exists
        return file_exists(args["path"])
    if name == "read_document":
        from tools.document_ops import read_document
        return await asyncio.to_thread(read_document, args["path"])

    # ---- window management ----
    if name == "list_windows":
        from tools.window_ops import list_windows
        return await list_windows()
    if name == "focus_window":
        from tools.window_ops import focus_window
        result = await focus_window(args["title"], strict=args.get("strict", False))
        if isinstance(result, dict) and not result.get("ok"):
            log.warning("focus_window failed: %s", result.get("error", result))
        return result
    if name == "get_active_window":
        from tools.window_ops import get_active_window
        return get_active_window()
    if name == "minimize_window":
        from tools.window_ops import minimize_window
        return await minimize_window(args["title"])
    if name == "maximize_window":
        from tools.window_ops import maximize_window
        return await maximize_window(args["title"])
    if name == "restore_window":
        from tools.window_ops import restore_window
        return await restore_window(args["title"])
    if name == "get_window_rect":
        from tools.window_ops import get_window_rect
        return await get_window_rect(args["title"])
    if name == "resize_window":
        from tools.window_ops import resize_window
        return await resize_window(args["title"], args["width"], args["height"])
    if name == "move_window":
        from tools.window_ops import move_window
        return await move_window(args["title"], args["x"], args["y"])

    # ---- mouse & keyboard ----
    if name == "mouse_click":
        from tools.input_ops import mouse_click
        return await mouse_click(args["x"], args["y"], args.get("button", "left"), args.get("double", False))
    if name == "mouse_move":
        from tools.input_ops import mouse_move
        return await mouse_move(args["x"], args["y"])
    if name == "mouse_scroll":
        from tools.input_ops import mouse_scroll
        return await mouse_scroll(args["clicks"])
    if name == "mouse_drag":
        from tools.input_ops import mouse_drag
        return await mouse_drag(args["x1"], args["y1"], args["x2"], args["y2"], args.get("button", "left"))
    if name == "keyboard_type":
        from tools.input_ops import keyboard_type
        result = await keyboard_type(args["text"], args.get("interval", 0.02))
        return result
    if name == "keyboard_hotkey":
        from tools.input_ops import keyboard_hotkey
        return await keyboard_hotkey(args["keys"])
    if name == "take_screenshot":
        from tools.input_ops import take_screenshot
        return await take_screenshot(args["output_path"], args.get("region"))
    if name == "find_on_screen":
        from tools.input_ops import find_on_screen
        return await find_on_screen(args["target"], args.get("confidence", 0.8))
    if name == "ocr_screen":
        from tools.input_ops import ocr_screen
        return await ocr_screen(args.get("region"))

    # ---- audio / media / notifications ----
    if name == "text_to_speech":
        from tools.media_ops import text_to_speech
        return await asyncio.to_thread(text_to_speech, args["text"], args.get("voice", ""))
    if name == "play_audio":
        from tools.media_ops import play_audio
        return await asyncio.to_thread(play_audio, args["path"])
    if name == "record_audio":
        from tools.media_ops import record_audio
        return await asyncio.to_thread(record_audio, args["output_path"], args["duration"])
    if name == "transcribe_local":
        from tools.web_search import transcribe_local
        return await asyncio.to_thread(transcribe_local, file_path=args["file_path"])
    if name == "show_notification":
        from tools.media_ops import show_notification
        return await asyncio.to_thread(show_notification, args["title"], args["message"], args.get("duration", 5))

    # ---- memory ----
    if name == "memory_store":
        return await asyncio.to_thread(mem.store, args["key"], args["value"])
    if name == "memory_retrieve":
        return await asyncio.to_thread(mem.retrieve, args.get("key", ""), args.get("search", ""))
    if name == "memory_delete":
        return await asyncio.to_thread(mem.delete, args["key"])
    if name == "memory_list":
        return await asyncio.to_thread(mem.list_all)

    # ---- goal management ----
    if name == "set_goal":
        global _active_goal, _consecutive_failures
        _active_goal = {"goal": args["goal"], "status": args.get("status", "active"), "steps": []}
        _consecutive_failures = 0
        return await asyncio.to_thread(
            mem.store, "active_goal",
            json.dumps({"goal": args["goal"], "status": args.get("status", "active")})
        )
    if name == "goal_status":
        global _active_goal
        _active_goal["status"] = args["status"]
        _active_goal["steps"].append(args["summary"])
        payload = {
            "goal":        _active_goal.get("goal"),
            "status":      args["status"],
            "summary":     args["summary"],
            "remaining":   args.get("remaining", ""),
            "next_action": args.get("next_action", ""),
        }
        await asyncio.to_thread(mem.store, "goal_status", json.dumps(payload))
        if args["status"] == "complete":
            return {"ok": True, "message": "Goal marked complete. Work is done.", **payload}
        if args["status"] == "blocked":
            return {"ok": True, "message": "Goal blocked — requires user input to continue.", **payload}
        return {
            "ok": True,
            "continue": True,
            "message": "Goal still active. Continue working.",
            **payload,
        }
    if name == "auth_guard":
        log.info("auth_guard: action=%s target=%s reason=%s", args["action"], args["target"], args["reason"])
        return {
            "requires_user_approval": True,
            "action": args["action"],
            "target": args["target"],
            "reason": args["reason"],
            "message": (
                f"APPROVAL REQUIRED: '{args['action']}' on '{args['target']}'. "
                f"Reason: {args['reason']}. "
                "Pause and wait for user to confirm before proceeding."
            ),
        }

    # ---- code & dev ----
    if name == "git_command":
        from tools.system_ops import git_command
        return await git_command(args["repo_path"], args["command"])
    if name == "pip_install":
        from tools.system_ops import pip_install
        return await pip_install(args["packages"])
    if name == "npm_command":
        from tools.system_ops import npm_command
        return await npm_command(args["project_path"], args["command"])
    if name == "start_dev_server":
        from tools.system_ops import start_dev_server
        return await start_dev_server(args["project_path"], args["command"], args.get("port", 3000))

    # ---- image / vision ----
    if name == "analyze_image":
        from tools.vision_ops import analyze_image
        return await asyncio.to_thread(analyze_image, args["path"], args.get("query", ""))
    if name == "generate_image":
        from tools.vision_ops import generate_image
        return await asyncio.to_thread(generate_image, args["prompt"], args["output_path"], args.get("width", 1024), args.get("height", 1024))
    if name == "convert_image":
        from tools.vision_ops import convert_image
        return await asyncio.to_thread(convert_image, args["input_path"], args["output_path"])
    if name == "resize_image":
        from tools.vision_ops import resize_image
        return await asyncio.to_thread(resize_image, args["input_path"], args["output_path"], args["width"], args["height"])

    # ---- browser automation ----
    if name == "browser_open":
        from tools.browser_ops import browser_open
        return await browser_open(args["url"], args.get("headless", True))
    if name == "browser_click":
        from tools.browser_ops import browser_click
        return await browser_click(args["selector"], args.get("selector_type", "css"))
    if name == "browser_type":
        from tools.browser_ops import browser_type
        return await browser_type(args["selector"], args["text"], args.get("selector_type", "css"))
    if name == "browser_get_html":
        from tools.browser_ops import browser_get_html
        return await browser_get_html()
    if name == "browser_screenshot":
        from tools.browser_ops import browser_screenshot
        return await browser_screenshot(args["output_path"])
    if name == "browser_execute_js":
        from tools.browser_ops import browser_execute_js
        return await browser_execute_js(args["script"])

    # ---- database ----
    if name == "db_query":
        from tools.db_ops import db_query
        return await asyncio.to_thread(db_query, args["db_path"], args["query"])
    if name == "db_execute":
        from tools.db_ops import db_execute
        return await asyncio.to_thread(db_execute, args["db_path"], args["statement"], args.get("parameters", []))

    # ---- networking & API ----
    if name == "http_get":
        from tools.web_search import http_get
        return await asyncio.to_thread(http_get, args["url"], args.get("headers", {}))
    if name == "http_post":
        from tools.web_search import http_post
        return await asyncio.to_thread(http_post, args["url"], args.get("body", {}), args.get("headers", {}), args.get("form", False))
    if name == "ping_host":
        from tools.web_search import ping_host
        return await asyncio.to_thread(ping_host, args["host"], args.get("count", 4))
    if name == "get_public_ip":
        from tools.web_search import get_public_ip
        return await asyncio.to_thread(get_public_ip)
    if name == "port_scan":
        from tools.web_search import port_scan
        return await asyncio.to_thread(port_scan, args["host"], args.get("start_port", 1), args.get("end_port", 1024))

    # ---- github ----
    if name == "github_search":
        from tools.web_search import github_search
        return await asyncio.to_thread(github_search, args["query"], args.get("search_type", "repositories"))
    if name == "github_get_file":
        from tools.web_search import github_get_file
        return await asyncio.to_thread(github_get_file, args["owner"], args["repo"], args["path"], args.get("branch", "main"))
    if name == "github_create_issue":
        from tools.web_search import github_create_issue
        return await asyncio.to_thread(github_create_issue, args["owner"], args["repo"], args["title"], args.get("body", ""))

    return {"error": f"Unknown tool: {name}"}


# ==========================================================================
# CALL TOOL HANDLER
# ==========================================================================

@app.call_tool()
async def call_tool(name: str, arguments: dict | None = None) -> list[types.TextContent]:
    global _consecutive_failures, _active_goal
    args = arguments or {}
    log.debug("call_tool: %s args=%s", name, list(args.keys()))

    if _consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
        msg = (
            f"Halted: {_consecutive_failures} consecutive tool failures. "
            "Diagnose the last error, call set_goal to restart, or ask the user for help."
        )
        log.warning(msg)
        return [types.TextContent(type="text", text=json.dumps({"error": msg}))]

    try:
        result = await _dispatch(name, args)
        _consecutive_failures = 0
        text = json.dumps(result, ensure_ascii=False, default=str)
        log.debug("call_tool OK: %s -> %d chars", name, len(text))
        return [types.TextContent(type="text", text=text)]
    except Exception as exc:
        _consecutive_failures += 1
        tb = traceback.format_exc()
        log.error("call_tool EXCEPTION [%s]: %s\n%s", name, exc, tb)
        return [types.TextContent(type="text", text=json.dumps({
            "error": str(exc),
            "traceback": tb,
            "tool": name,
            "consecutive_failures": _consecutive_failures,
        }))]


# ==========================================================================
# ENTRY POINT
# ==========================================================================

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream, write_stream,
            app.create_initialization_options()
        )

if __name__ == "__main__":
    asyncio.run(main())
