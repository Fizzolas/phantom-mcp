"""
tools/internet_router.py  —  Smart internet vs local routing

Phantom calls needs_internet(query) automatically BEFORE using any search
tool to decide the cheapest path to an answer:

  LOCAL   → answer from Gemma training data  (no network call)
  INTERNET → call one of the 38 web tools in web_search.py

Decision engine (priority order):
  1. URL in query                      → internet (visit_page)
  2. Real-time / live-data keywords    → internet (specific tool)
  3. Post-cutoff date detected         → internet (google_search)
  4. Research / lookup phrases         → internet (google_search or wikipedia)
  5. Evergreen / conceptual question   → local

suggested_tool maps to the EXACT function name in web_search.py so the
model can call it without guessing.

The model is free to OVERRIDE — this is a hint, not a hard rule.
If the model is confident it knows the answer, it may skip the internet call.
If it is uncertain, it MUST call needs_internet first then the suggested tool.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

# ── Model knowledge cutoff (approximate) ─────────────────────────────────────
# Gemma 4 training data ends roughly early 2025.  Anything after this date
# is outside the model's knowledge and MUST be fetched from the internet.
_CUTOFF_YEAR = 2025
_CUTOFF_MONTH = 4  # April 2025 — conservative estimate

# ── Signal word tables ────────────────────────────────────────────────────────
_REALTIME = [
    # live data
    "price", "stock price", "stock market", "nasdaq", "nyse", "crypto",
    "bitcoin", "ethereum", "weather", "forecast", "temperature",
    "score", "result", "game today", "match today",
    # temporal
    "right now", "at this moment", "currently", "today", "tonight",
    "this week", "this month", "this year", "latest", "most recent",
    "breaking", "just happened", "just released", "just announced",
    "new release", "new version", "patch notes", "changelog",
    "trending", "viral", "update",
    # commerce / travel
    "flight", "hotel", "cheapest", "best deal", "in stock", "buy now",
    "shipping", "delivery time", "track package",
    # news
    "news", "headline",
]

_RESEARCH = [
    "who is", "who was", "what is", "what are", "what does",
    "where is", "where was", "how does", "how do", "how did",
    "when did", "when was", "when is",
    "why does", "why did", "why is",
    "explain", "define", "definition of",
    "find me", "search for", "look up", "look it up",
    "google", "google it", "search google",
    "paper", "research paper", "academic", "study", "journal",
    "wikipedia", "wiki",
    "biography", "history of",
]

_URL_RE = re.compile(
    r"https?://|www\.|\.(com|org|net|io|co|gov|edu|dev|app|ai)(\s|/|$)",
    re.I,
)

# ── Tool routing map ──────────────────────────────────────────────────────────
# Maps a keyword → the best first tool to call from web_search.py
_TOOL_MAP: dict[str, str] = {
    # weather
    "weather": "google_weather",
    "forecast": "google_weather",
    "temperature": "google_weather",
    # finance
    "stock": "google_finance",
    "nasdaq": "google_finance",
    "nyse": "google_finance",
    "crypto": "google_finance",
    "bitcoin": "google_finance",
    "ethereum": "google_finance",
    "price": "google_shopping",
    # news
    "news": "google_news",
    "headline": "google_news",
    "breaking": "google_news",
    # travel
    "flight": "google_flights",
    "hotel": "google_hotels",
    # shopping
    "buy": "google_shopping",
    "shop": "google_shopping",
    "in stock": "google_shopping",
    "cheapest": "google_shopping",
    # academic
    "paper": "google_scholar",
    "research paper": "google_scholar",
    "academic": "google_scholar",
    "journal": "google_scholar",
    # reference
    "wikipedia": "wikipedia",
    "wiki": "wikipedia",
    "define": "wikipedia",
    "definition": "wikipedia",
    # maps
    "map": "google_maps",
    "directions": "google_maps_directions",
    "route": "google_maps_directions",
    "navigate": "google_maps_directions",
    # media
    "image": "google_images",
    "picture": "google_images",
    "photo": "google_images",
    # trends
    "trend": "google_trends",
    "trending": "google_trends",
    # translate
    "translate": "google_translate",
    "translation": "google_translate",
    # books
    "book": "google_books",
    # video
    "youtube": "transcribe_video",
    "video": "transcribe_video",
    "transcript": "transcribe_video",
}


def needs_internet(query: str) -> dict:
    """
    Decide whether a query requires a live internet call.

    Returns:
      {
        "decision": "internet" | "local",
        "reason":   str,
        "suggested_tool": str | None,   # exact function name in web_search.py
        "confidence": "high" | "medium" | "low"
      }

    The model MUST:
      - Call the suggested_tool if decision == 'internet'
      - Answer from training if decision == 'local'
      - Always be free to override with its own judgment
    """
    q = query.lower().strip()

    # ── Rule 1: URL present ───────────────────────────────────────────────────
    if _URL_RE.search(q):
        return {
            "decision": "internet",
            "reason": "Query contains a URL — fetch it directly.",
            "suggested_tool": "visit_page",
            "confidence": "high",
        }

    # ── Rule 2: Real-time keywords ────────────────────────────────────────────
    for word in _REALTIME:
        if word in q:
            tool = _pick_tool(q)
            return {
                "decision": "internet",
                "reason": f"Real-time keyword '{word}' detected — live data required.",
                "suggested_tool": tool,
                "confidence": "high",
            }

    # ── Rule 3: Post-cutoff date in query ─────────────────────────────────────
    year_match = re.search(r"\b(202[5-9]|20[3-9]\d)\b", q)
    if year_match:
        year = int(year_match.group())
        if year > _CUTOFF_YEAR or (year == _CUTOFF_YEAR and _month_hint(q) > _CUTOFF_MONTH):
            return {
                "decision": "internet",
                "reason": f"Year {year} is beyond training cutoff ({_CUTOFF_YEAR}/{_CUTOFF_MONTH:02d}).",
                "suggested_tool": "google_search",
                "confidence": "high",
            }

    # ── Rule 4: Research / lookup phrases ────────────────────────────────────
    for phrase in _RESEARCH:
        if phrase in q:
            tool = _pick_tool(q)
            return {
                "decision": "internet",
                "reason": f"Research phrase '{phrase}' detected — verify with live source.",
                "suggested_tool": tool,
                "confidence": "medium",
            }

    # ── Rule 5: Evergreen — answer locally ───────────────────────────────────
    return {
        "decision": "local",
        "reason": "No real-time or research signals detected. Answer from training knowledge.",
        "suggested_tool": None,
        "confidence": "medium",
    }


def _pick_tool(q: str) -> str:
    """Select the most specific tool for a query."""
    for keyword, tool in _TOOL_MAP.items():
        if keyword in q:
            return tool
    return "google_search"  # sensible default


def _month_hint(q: str) -> int:
    """Extract a rough month number from a query string, or return 0."""
    months = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
        "jan": 1, "feb": 2, "mar": 3, "apr": 4,
        "jun": 6, "jul": 7, "aug": 8, "sep": 9,
        "oct": 10, "nov": 11, "dec": 12,
    }
    for name, num in months.items():
        if name in q:
            return num
    return 0


# ── Convenience: list all web tools ──────────────────────────────────────────
def list_web_tools() -> list[str]:
    """
    Return every tool name available in web_search.py.
    Useful for the model to know what it can call.
    """
    return [
        # Search & Web
        "google_search", "google_news", "google_scholar",
        "google_images", "google_trends", "visit_page",
        # Travel & Commerce
        "google_shopping", "google_flights", "google_hotels",
        "google_translate", "google_maps", "google_maps_directions",
        # Finance & Info
        "google_finance", "google_weather", "google_books",
        # Vision & OCR
        "google_lens", "google_lens_detect", "ocr_image", "list_images",
        # Video & Audio
        "transcribe_video", "transcribe_local", "search_transcript",
        "extract_video_clip", "convert_media",
        # Documents
        "read_document",
        # Email
        "fetch_emails",
        # Web Utilities
        "paste_text", "shorten_url", "generate_qr",
        "archive_webpage", "wikipedia",
        # Cloud
        "upload_to_s3",
        # Feeds
        "subscribe", "unsubscribe", "list_subscriptions",
        "check_feeds", "search_feeds", "get_feed_items",
    ]
