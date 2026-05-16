"""
LM Studio / Jan.ai runtime probe.

At boot (and on demand when list_tools is called) we discover:

  * Whether the AI host's local server is reachable.
  * The currently-loaded model's id.
  * The model's maximum context length — used by the token budget so tool
    outputs never exceed it.
  * Whether the model advertises tool-use (not every local model does).

Supported hosts (auto-detected in order):
  1. LM Studio SDK (async path, most accurate context length)
  2. LM Studio REST  http://localhost:1234/v1
  3. Jan.ai REST     http://localhost:1337/v1
  4. Any PHANTOM_HOST_URL override

Phase 3 fixes:
  Issue 1 — Jan.ai REST: the Jan.ai /v1/models response schema differs from
             LM Studio's. Jan doesn't include 'loaded_context_length' or
             'state' fields. _probe_via_rest() now has a Jan-specific
             fallback: it reads 'context_length' from the model's top-level
             fields, and treats any reachable model as loaded.
  Issue 2 — No-model-loaded hint: when the server is reachable but no model
             is loaded, the probe now logs a warning with an actionable
             message ("load a model in LM Studio/Jan.ai") instead of just
             setting error= and moving on silently.
  Issue 3 — Stale-cache invalidation: if a new probe returns a different
             model_id from the cached one, the cache is force-refreshed and
             a log.info() fires so the server.log shows the model swap.
  Issue 4 — REST supports_tools fallback: Jan.ai's model objects don't
             always have a 'capabilities.tools' field. We now default to
             True for REST probes (the model advertises itself; if it
             doesn't support tools the call will just fail gracefully)
             but only when the field is genuinely absent.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field, asdict
from typing import Any

log = logging.getLogger(__name__)

LMS_DEFAULT_BASE = "http://localhost:1234/v1"
JAN_DEFAULT_BASE = "http://localhost:1337/v1"
PROBE_TTL_S = 15.0
PROBE_TIMEOUT_S = 2.5

# Conservative fallback when we truly can't tell.
FALLBACK_CONTEXT_LENGTH = 8192


@dataclass
class LMStudioProbe:
    reachable: bool = False
    model_id: str | None = None
    context_length: int = FALLBACK_CONTEXT_LENGTH
    supports_tools: bool = False
    base_url: str = LMS_DEFAULT_BASE
    probed_at: float = 0.0
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_cache: LMStudioProbe | None = None
# Lazy asyncio.Lock — created on first async access after event loop starts.
_cache_lock: asyncio.Lock | None = None


async def probe_lmstudio(
    base_url: str = LMS_DEFAULT_BASE,
    *,
    force: bool = False,
) -> LMStudioProbe:
    """
    Return a fresh LMStudioProbe, cached for PROBE_TTL_S.

    `force=True` bypasses the TTL cache (use when the user swaps models
    or the server suspects staleness).

    Phase 3 Issue 3: if the new probe returns a different model_id than
    the cached one, the cache is always updated and a log.info fires.
    """
    global _cache, _cache_lock
    if _cache_lock is None:
        _cache_lock = asyncio.Lock()

    async with _cache_lock:
        now = time.time()
        if (
            not force
            and _cache is not None
            and _cache.base_url == base_url
            and (now - _cache.probed_at) < PROBE_TTL_S
        ):
            return _cache

        fresh = await _probe_once(base_url)

        # Phase 3 Issue 3: detect model swap and log it.
        if (
            _cache is not None
            and fresh.reachable
            and fresh.model_id
            and fresh.model_id != _cache.model_id
        ):
            log.info(
                "lmstudio probe: model changed %r → %r (ctx=%d tools=%s)",
                _cache.model_id, fresh.model_id,
                fresh.context_length, fresh.supports_tools,
            )

        _cache = fresh
        return _cache


async def _probe_once(base_url: str) -> LMStudioProbe:
    """
    Try the LM Studio SDK first (gives us get_context_length directly),
    then fall back to the OpenAI-compatible /models REST endpoint.
    """
    sdk_probe = await _probe_via_sdk()
    if sdk_probe is not None and sdk_probe.reachable:
        sdk_probe.base_url = base_url
        sdk_probe.probed_at = time.time()
        return sdk_probe

    rest_probe = await _probe_via_rest(base_url)
    rest_probe.probed_at = time.time()
    return rest_probe


def _sdk_model_supports_tools(model: Any) -> bool:
    """
    Inspect the SDK model object for actual tool-use capability.
    Priority order:
      1. model.supports_tool_use  (direct bool attribute)
      2. model.capabilities["tools"]  (dict attribute)
      3. False  (conservative default)
    """
    val = getattr(model, "supports_tool_use", None)
    if val is not None:
        return bool(val)
    caps = getattr(model, "capabilities", None)
    if isinstance(caps, dict):
        return bool(caps.get("tools", False))
    return False


async def _probe_via_sdk() -> LMStudioProbe | None:
    """
    Preferred path: LM Studio official Python SDK.
    Uses AsyncClient to avoid 'event loop already running' errors.
    Returns None if the SDK isn't installed or AsyncClient is unavailable.
    """
    try:
        import lmstudio  # type: ignore
    except Exception:
        return None

    try:
        async_client_cls = getattr(lmstudio, "AsyncClient", None)
        if async_client_cls is None:
            return None

        async with async_client_cls() as client:
            model = await client.llm.model()
            ctx = int(await model.get_context_length())
            mid = (
                getattr(model, "identifier", None)
                or getattr(model, "id", None)
                or "unknown"
            )
            has_tools = _sdk_model_supports_tools(model)
            return LMStudioProbe(
                reachable=True,
                model_id=str(mid),
                context_length=ctx,
                supports_tools=has_tools,
                raw={"via": "sdk_async", "supports_tools_raw": has_tools},
            )
    except Exception as e:
        log.debug("sdk async probe failed: %s", e)
        return LMStudioProbe(
            reachable=False,
            error=f"sdk async probe failed: {e!s}",
            raw={"via": "sdk_async"},
        )


async def _probe_via_rest(base_url: str) -> LMStudioProbe:
    """
    Fallback: OpenAI-compatible REST /v1/models endpoint.

    Handles both LM Studio and Jan.ai response shapes:
    - LM Studio: 'state', 'loaded_context_length', 'max_context_length',
                 'capabilities.tools'
    - Jan.ai:    no 'state' field; uses 'context_length' at top level;
                 may not have 'capabilities'

    Phase 3 Issue 4: supports_tools defaults to True when the 'capabilities'
    field is absent (Jan.ai doesn't include it; we assume the model supports
    tools and let the call fail gracefully if it doesn't).
    """
    try:
        import httpx
    except Exception as e:
        return LMStudioProbe(
            reachable=False, error=f"httpx missing: {e!s}", raw={"via": "rest"}
        )

    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_S) as client:
            resp = await client.get(f"{base_url}/models")
            resp.raise_for_status()
            payload = resp.json()
    except Exception as e:
        return LMStudioProbe(
            reachable=False,
            base_url=base_url,
            error=f"rest probe failed: {e!s}",
            raw={"via": "rest"},
        )

    data = payload.get("data") or []

    # LM Studio marks the active model with state='loaded'.
    # Jan.ai has no state field — just take the first model in the list.
    loaded = (
        next((m for m in data if m.get("state") == "loaded"), None)
        or (data[0] if data else None)
    )

    if loaded is None:
        # Phase 3 Issue 2: actionable warning, not silent.
        log.warning(
            "REST probe at %s: server reachable but no model is loaded. "
            "Load a model in LM Studio or Jan.ai before using Phantom.",
            base_url,
        )
        return LMStudioProbe(
            reachable=True,
            base_url=base_url,
            error="no model loaded",
            raw={"via": "rest", "models_found": 0},
        )

    # Phase 3 Issue 1: Jan.ai context length field order.
    ctx = (
        loaded.get("loaded_context_length")   # LM Studio
        or loaded.get("max_context_length")    # LM Studio fallback
        or loaded.get("context_length")        # Jan.ai
        or FALLBACK_CONTEXT_LENGTH
    )

    # Phase 3 Issue 4: supports_tools — default True when field absent.
    capabilities = loaded.get("capabilities")
    if capabilities is None:
        # Jan.ai doesn't send capabilities — assume tools supported.
        supports_tools = True
    else:
        supports_tools = bool(capabilities.get("tools", True))

    model_id = str(loaded.get("id", "unknown"))
    log.debug(
        "REST probe at %s: model=%s ctx=%s tools=%s",
        base_url, model_id, ctx, supports_tools,
    )

    return LMStudioProbe(
        reachable=True,
        model_id=model_id,
        context_length=int(ctx),
        supports_tools=supports_tools,
        base_url=base_url,
        raw={"via": "rest", "loaded": loaded},
    )
