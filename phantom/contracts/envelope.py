"""
ToolResult — the single response shape every phantom tool returns.

Design rules (see docs/refactor-plan.md):
  * Never raise out of a tool. Wrap in ok(...) or fail(...).
  * `error` and `hint` are model-readable sentences, not stack traces.
  * `data` is what the model consumes; it MAY be truncated by the runtime
    budget manager, in which case meta['truncated'] is True and meta
    carries a `continuation_token` when the tool supports resumption.
  * `meta` is observability-only: timings, source URLs, truncation flags,
    model id used for self-summarization, etc.

The envelope serializes to JSON cleanly via dataclasses.asdict.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class ToolResult:
    ok: bool
    data: Any = None
    error: str | None = None
    hint: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    # Convenience: lets the MCP layer treat a ToolResult like a plain mapping
    # when serializing to JSON-RPC responses.
    def __iter__(self):
        return iter(self.to_dict().items())


def ok(data: Any = None, *, hint: str | None = None, **meta: Any) -> ToolResult:
    """Success envelope. Extra kwargs land in meta."""
    return ToolResult(ok=True, data=data, hint=hint, meta=dict(meta))


def fail(
    error: str,
    *,
    hint: str | None = None,
    category: str = "server_error",
    **meta: Any,
) -> ToolResult:
    """Failure envelope. `error` should be a short actionable sentence."""
    m = {"category": category, **meta}
    return ToolResult(ok=False, error=error, hint=hint, meta=m)
