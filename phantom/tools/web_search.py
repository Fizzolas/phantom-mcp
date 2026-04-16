"""
phantom.tools.web_search — Google web search.

Demonstrates:
  * Wrapping an async legacy function correctly. The original server.py
    bug was calling asyncio.to_thread(async_fn, ...) which produced an
    unawaited coroutine. Here the registry sees is_async=True and the
    executor awaits it properly.
  * Structured input with enums — `time_range` becomes a Literal,
    rejecting garbage like 'yesterday' before a network call is made.
  * Fallback hint. If the search fails, the envelope tells the model
    to try visit_page or google_news instead.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from phantom.contracts import fail, ok
from phantom.tools._base import tool


TimeRange = Literal["", "past_hour", "past_day", "past_week", "past_month", "past_year"]


class WebSearchInput(BaseModel):
    query: str = Field(..., min_length=1, description="Search query.")
    num_results: int = Field(5, ge=1, le=20)
    time_range: TimeRange = Field("", description="Restrict results by recency.")
    site: str = Field("", description="Restrict to a single domain, e.g. 'reddit.com'.")
    page: int = Field(1, ge=1, le=5)
    language: str = Field("en", min_length=2, max_length=5)
    region: str = Field("us", min_length=2, max_length=5)

    model_config = ConfigDict(extra="forbid")


@tool(
    "web_search",
    category="web",
    schema=WebSearchInput,
    needs=("playwright",),
    timeout_s=45.0,
)
async def web_search(
    query: str,
    num_results: int = 5,
    time_range: str = "",
    site: str = "",
    page: int = 1,
    language: str = "en",
    region: str = "us",
) -> dict:
    """
    Google web search. Returns a list of {title, url, snippet}.

    Use when the user asks for current information, news, reviews, or
    factual lookups. For reading a specific URL, prefer visit_page.
    For structured news, prefer google_news.
    """
    from tools.web_search import google_search as legacy_search

    result = await legacy_search(
        query=query,
        num_results=num_results,
        time_range=time_range,
        site=site,
        page=page,
        language=language,
        region=region,
    )
    # The legacy function signals failure via an "error" key; normalize it.
    if isinstance(result, dict) and result.get("error"):
        return fail(
            str(result["error"]),
            hint="Try visit_page on a known URL, or narrow the query.",
            category="external_error",
        )
    return ok(result)
