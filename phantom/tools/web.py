"""
phantom.tools.web — web search & page fetching.

The legacy web_search.py has ~35 functions. Most are undocumented,
untested variants of the same behavior. PR 3 exposes a curated subset
and collapses variants behind enums.

What ships:
  web_search       query Google with kind=('web','news','scholar','images','shopping','books')
  visit_page       fetch and render a URL; returns extracted text
  google_trends    trend scores for a query
  google_maps      geocoded results
  google_finance   finance quotes / summary
  google_weather   current weather for a location
  google_translate text translation

What does NOT ship (deliberately un-exposed):
  * amazon/ebay/craigslist/youtube/twitter/reddit/linkedin — the legacy
    functions for these either don't exist or return garbage. They were
    advertised in README and SYSTEM_PROMPT but not implemented.
  * send_email / calendar_events / stock_price / crypto_price /
    currency_convert / translate_text / get_weather — ghost names for
    real functions (google_finance, google_weather, google_translate).
  * download_youtube / extract_video_clip / fetch_emails — side-effectful
    tools better served by dedicated files PRs.

phantom_web_search in PR 1 stays — this file adds the others. The two
coexist because removing phantom_web_search would break users of PR 1.
It's aliased to web_search(kind='web') so the new preferred form is clear.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from phantom.contracts import fail, ok
from phantom.tools._base import tool


SearchKind = Literal["web", "news", "scholar", "images", "shopping", "books"]


# ---------------------------------------------------------------------------
# Search — the one multi-kind tool
# ---------------------------------------------------------------------------


class WebSearchUnifiedInput(BaseModel):
    query: str = Field(..., min_length=1)
    kind: SearchKind = Field("web", description="Which Google vertical to query.")
    num_results: int = Field(5, ge=1, le=20)
    time_range: Literal["", "past_hour", "past_day", "past_week", "past_month", "past_year"] = Field("")
    site: str = Field("")
    page: int = Field(1, ge=1, le=5)
    language: str = Field("en", min_length=2, max_length=5)
    region: str = Field("us", min_length=2, max_length=5)

    model_config = ConfigDict(extra="forbid")


@tool("search", category="web", schema=WebSearchUnifiedInput, needs=("playwright",), timeout_s=60.0)
async def search(
    query: str,
    kind: str = "web",
    num_results: int = 5,
    time_range: str = "",
    site: str = "",
    page: int = 1,
    language: str = "en",
    region: str = "us",
) -> dict:
    """
    Unified search tool. Pick `kind`:

      web       general web results
      news      news articles with dates
      scholar   academic papers
      images    image results with thumbnails
      shopping  product listings
      books     book search

    Returns a list of hits shaped per kind. For reading a specific URL,
    prefer visit_page. For structured finance / weather / trends, use
    the dedicated tools.
    """
    from tools.web_search import (
        google_search, google_news, google_scholar,
        google_images, google_shopping, google_books,
    )

    dispatch = {
        "web": google_search,
        "news": google_news,
        "scholar": google_scholar,
        "images": google_images,
        "shopping": google_shopping,
        "books": google_books,
    }
    fn = dispatch[kind]

    if kind == "web":
        result = await fn(
            query=query, num_results=num_results, time_range=time_range,
            site=site, page=page, language=language, region=region,
        )
    else:
        result = await fn(query=query, num_results=num_results)

    if isinstance(result, dict) and result.get("error"):
        return fail(
            str(result["error"]),
            hint="Try a narrower query, or use visit_page on a known URL.",
            category="external_error",
        )
    return ok(result)


# ---------------------------------------------------------------------------
# Visit a URL
# ---------------------------------------------------------------------------


class VisitPageInput(BaseModel):
    url: str = Field(..., pattern=r"^https?://", description="Full URL including scheme.")
    model_config = ConfigDict(extra="forbid")


@tool("visit_page", category="web", schema=VisitPageInput, needs=("playwright",), timeout_s=60.0)
async def visit_page(url: str) -> dict:
    """
    Fetch a URL and return the extracted main-content text.

    Use after a search returns a promising URL, or when the user gives
    one directly. The TokenBudget layer will summarize long pages
    automatically in PR 4.
    """
    from tools.web_search import visit_page as legacy

    result = await legacy(url=url)
    if isinstance(result, dict) and result.get("error"):
        return fail(
            str(result["error"]),
            hint="Verify the URL is reachable; try search to find an alternative source.",
            category="external_error",
        )
    return ok(result)


# ---------------------------------------------------------------------------
# Structured query tools (one-shot typed answers)
# ---------------------------------------------------------------------------


class QueryOnlyInput(BaseModel):
    query: str = Field(..., min_length=1)
    model_config = ConfigDict(extra="forbid")


class LocationInput(BaseModel):
    location: str = Field(..., min_length=1, description="City, zip code, or 'lat,lon'.")
    model_config = ConfigDict(extra="forbid")


class TranslateInput(BaseModel):
    text: str = Field(..., min_length=1)
    source: str = Field("auto", description="ISO code or 'auto' to detect.")
    target: str = Field(..., min_length=2, max_length=5, description="ISO code.")
    model_config = ConfigDict(extra="forbid")


class MapsInput(BaseModel):
    query: str = Field(..., min_length=1)
    num_results: int = Field(5, ge=1, le=20)
    model_config = ConfigDict(extra="forbid")


@tool("trends", category="web", schema=QueryOnlyInput, needs=("playwright",), timeout_s=45.0)
async def trends(query: str) -> dict:
    """Google Trends interest-over-time for `query`."""
    from tools.web_search import google_trends as legacy

    return ok(await legacy(query=query))


@tool("maps", category="web", schema=MapsInput, needs=("playwright",), timeout_s=45.0)
async def maps(query: str, num_results: int = 5) -> dict:
    """Google Maps places search — returns name, address, rating, URL."""
    from tools.web_search import google_maps as legacy

    return ok(await legacy(query=query, num_results=num_results))


@tool("finance", category="web", schema=QueryOnlyInput, needs=("playwright",), timeout_s=45.0)
async def finance(query: str) -> dict:
    """
    Google Finance quote lookup. `query` can be a ticker ('NVDA'), a
    company name ('Nvidia'), or a currency pair ('USD/EUR').
    """
    from tools.web_search import google_finance as legacy

    return ok(await legacy(query=query))


@tool("weather", category="web", schema=LocationInput, needs=("playwright",), timeout_s=45.0)
async def weather(location: str) -> dict:
    """Current weather + short-term forecast for a location."""
    from tools.web_search import google_weather as legacy

    return ok(await legacy(location=location))


@tool("translate", category="web", schema=TranslateInput, needs=("playwright",), timeout_s=30.0)
async def translate(text: str, target: str, source: str = "auto") -> dict:
    """Translate `text` from `source` to `target` (ISO-639 language codes)."""
    from tools.web_search import google_translate as legacy

    return ok(await legacy(text=text, source=source, target=target))
