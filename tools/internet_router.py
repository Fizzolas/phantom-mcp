"""
tools/internet_router.py  —  Internet vs Local routing decision helper

The model uses this to decide whether a question needs a live internet
call or can be answered from its own training / memory.

Logic:
  needs_internet(query) returns a dict with:
    decision:  'internet' | 'local'
    reason:    short explanation
    suggested_tool: best first tool to call (if internet)

Decision rules (in priority order):
  1. Explicit real-time triggers       → internet
     (price, stock, weather, score, "right now", "today", "latest", etc.)
  2. Proper nouns + recency context    → internet
  3. URL / domain present in query     → internet  (visit_page)
  4. Knowledge cutoff boundary         → internet
     (events after 2024-04-01 heuristic)
  5. Ambiguous / evergreen knowledge   → local

The model is free to override this by calling internet tools directly.
This helper is exposed as a tool so the model can explicitly ask:
  needs_internet(query="What is the current Bitcoin price?")
and get a structured hint before committing tokens to a tool chain.
"""
from __future__ import annotations
import re

# ─── signal word lists ────────────────────────────────────────────────────────
_REALTIME = [
    "price", "stock", "weather", "forecast", "score", "live", "stream",
    "right now", "today", "tonight", "current", "latest", "breaking",
    "just released", "new release", "just announced", "trending",
    "flight", "hotel", "buy", "shop", "shipping", "in stock",
    "news", "update", "patch", "version", "changelog", "release notes",
]
_RESEARCH = [
    "who is", "what is", "where is", "how does", "when did", "why does",
    "find me", "search for", "look up", "google", "search google",
    "paper", "article", "study", "research",
    "wikipedia", "wiki",
]
_URL_RE = re.compile(r"https?://|www\.|\.(com|org|net|io|co|gov|edu)/", re.I)

_TOOL_MAP = {
    "weather": "google_weather",
    "forecast": "google_weather",
    "price": "google_shopping",
    "stock": "google_finance",
    "news": "google_news",
    "flight": "google_flights",
    "hotel": "google_hotels",
    "buy": "google_shopping",
    "shop": "google_shopping",
    "paper": "google_scholar",
    "research": "google_scholar",
    "wikipedia": "wikipedia",
    "wiki": "wikipedia",
    "translate": "google_translate",
    "map": "google_maps",
    "direction": "google_maps_directions",
    "route": "google_maps_directions",
    "image": "google_images",
    "trend": "google_trends",
    "book": "google_books",
}


def needs_internet(query: str) -> dict:
    q = query.lower()

    # 1. URL present → always fetch
    if _URL_RE.search(q):
        return {
            "decision": "internet",
            "reason": "Query contains a URL — use visit_page to fetch it.",
            "suggested_tool": "visit_page",
        }

    # 2. Real-time signal words
    for word in _REALTIME:
        if word in q:
            tool = next((v for k, v in _TOOL_MAP.items() if k in q), "google_search")
            return {
                "decision": "internet",
                "reason": f"Real-time keyword '{word}' detected — live data needed.",
                "suggested_tool": tool,
            }

    # 3. Research signal words
    for phrase in _RESEARCH:
        if phrase in q:
            tool = next((v for k, v in _TOOL_MAP.items() if k in q), "google_search")
            return {
                "decision": "internet",
                "reason": f"Research phrase '{phrase}' detected.",
                "suggested_tool": tool,
            }

    # 4. Default → answer from training / memory
    return {
        "decision": "local",
        "reason": "No real-time or research signals detected. Answering from training knowledge.",
        "suggested_tool": None,
    }
