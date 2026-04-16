"""
LM Studio runtime probe.

At boot (and on demand, when the list_tools endpoint is called) we discover:

  * Whether LM Studio's local server is reachable at all.
  * The currently-loaded model's id.
  * The model's maximum context length — used by the token budget so tool
    outputs never blow past it. See:
    https://lmstudio.ai/docs/python/model-info/get-context-length
  * Whether the model advertises tool-use (not every local model does).

We prefer the LM Studio Python SDK when present, and fall back to the
OpenAI-compatible REST API at http://localhost:1234/v1 otherwise. The
probe is cheap (<100ms on a warm server) but results are cached with a
short TTL so we don't hammer LM Studio every time list_tools is called.

If LM Studio is unreachable, the probe returns a degraded-but-valid
snapshot — the server keeps working, tools that don't need the model
(shell, file ops, clipboard) stay available.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field, asdict
from typing import Any

LMS_DEFAULT_BASE = "http://localhost:1234/v1"
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
_cache_lock = asyncio.Lock()


async def probe_lmstudio(
    base_url: str = LMS_DEFAULT_BASE,
    *,
    force: bool = False,
) -> LMStudioProbe:
    """
    Return a fresh LMStudioProbe, cached for PROBE_TTL_S.

    `force=True` bypasses the cache (call this when the user explicitly
    swaps models or the server suspects staleness).
    """
    global _cache
    async with _cache_lock:
        now = time.time()
        if (
            not force
            and _cache is not None
            and _cache.base_url == base_url
            and (now - _cache.probed_at) < PROBE_TTL_S
        ):
            return _cache
        _cache = await _probe_once(base_url)
        return _cache


async def _probe_once(base_url: str) -> LMStudioProbe:
    """
    Try the LM Studio SDK first (gives us get_context_length directly),
    then fall back to the OpenAI-compatible /models endpoint.
    """
    sdk_probe = await _probe_via_sdk()
    if sdk_probe is not None:
        sdk_probe.base_url = base_url
        sdk_probe.probed_at = time.time()
        return sdk_probe

    rest_probe = await _probe_via_rest(base_url)
    rest_probe.probed_at = time.time()
    return rest_probe


async def _probe_via_sdk() -> LMStudioProbe | None:
    """
    Preferred path: LM Studio's official Python SDK. Surfaces exact context
    length via model.get_context_length().
    """
    try:
        import lmstudio  # type: ignore
    except Exception:
        return None

    def _work() -> LMStudioProbe:
        try:
            client = lmstudio.Client()  # type: ignore[attr-defined]
            # `llm.model()` returns the currently loaded model handle.
            model = client.llm.model()
            ctx = int(model.get_context_length())
            mid = getattr(model, "identifier", None) or getattr(model, "id", None) or "unknown"
            return LMStudioProbe(
                reachable=True,
                model_id=str(mid),
                context_length=ctx,
                supports_tools=True,  # SDK path implies tool-use is available.
                raw={"via": "sdk"},
            )
        except Exception as e:
            return LMStudioProbe(
                reachable=False,
                error=f"sdk probe failed: {e!s}",
                raw={"via": "sdk"},
            )

    try:
        return await asyncio.wait_for(asyncio.to_thread(_work), timeout=PROBE_TIMEOUT_S)
    except asyncio.TimeoutError:
        return LMStudioProbe(
            reachable=False,
            error="sdk probe timed out",
            raw={"via": "sdk"},
        )


async def _probe_via_rest(base_url: str) -> LMStudioProbe:
    """
    Fallback: OpenAI-compatible REST. LM Studio exposes `/v1/models` and
    each entry includes `loaded_context_length` and `max_context_length`.
    """
    try:
        import httpx  # lazy import; httpx is already a project dep
    except Exception as e:
        return LMStudioProbe(reachable=False, error=f"httpx missing: {e!s}", raw={"via": "rest"})

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
    # Prefer the model LM Studio reports as loaded; otherwise take the first.
    loaded = next((m for m in data if m.get("state") == "loaded"), None) or (data[0] if data else None)

    if loaded is None:
        return LMStudioProbe(
            reachable=True,
            base_url=base_url,
            error="no model loaded in LM Studio",
            raw={"via": "rest", "models": []},
        )

    ctx = (
        loaded.get("loaded_context_length")
        or loaded.get("max_context_length")
        or loaded.get("context_length")
        or FALLBACK_CONTEXT_LENGTH
    )

    return LMStudioProbe(
        reachable=True,
        model_id=str(loaded.get("id", "unknown")),
        context_length=int(ctx),
        supports_tools=bool(loaded.get("capabilities", {}).get("tools", True)),
        base_url=base_url,
        raw={"via": "rest", "loaded": loaded},
    )
