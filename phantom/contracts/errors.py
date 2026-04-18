"""
Structured error classification for phantom tools.

Follows the MCP best-practice split (https://modelcontextprotocol.info/docs/best-practices/):

  CLIENT_ERROR   — caller's fault: bad args, missing permissions, unknown key.
                   Usually not retryable. Model should correct its call.
  SERVER_ERROR   — our fault: bug, invariant violation, unexpected exception.
                   Log loudly; advise the model to try a fallback tool.
  EXTERNAL_ERROR — a dependency we don't control failed: network, LM Studio
                   down, Playwright crashed, OCR binary missing at runtime.
                   Retryable with backoff; may resolve on its own.

`classify(exc)` maps common Python exceptions to a category so individual
tools don't have to repeat that logic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ErrorCategory(str, Enum):
    CLIENT_ERROR = "client_error"
    SERVER_ERROR = "server_error"
    EXTERNAL_ERROR = "external_error"


@dataclass
class MCPError:
    category: ErrorCategory
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    retry_after: float | None = None  # seconds; None means "don't auto-retry"

    def as_envelope_fields(self) -> dict[str, Any]:
        """Shape suitable for ToolResult(fail=..., **meta)."""
        return {
            "error": self.message,
            "category": self.category.value,
            "code": self.code,
            "details": self.details,
            "retry_after": self.retry_after,
        }


# Exception types we treat as transient/external. Imported lazily so
# phantom doesn't pull heavy deps just to classify.
_EXTERNAL_EXC_NAMES = {
    "TimeoutError",
    "ConnectionError",
    "ConnectionRefusedError",
    "ConnectionResetError",
    "ConnectTimeout",
    "ReadTimeout",
    "HTTPError",
    "RemoteDisconnected",
    "SSLError",
}

_CLIENT_EXC_NAMES = {
    "ValueError",
    "TypeError",
    "KeyError",
    "PermissionError",
    "FileNotFoundError",
    "NotADirectoryError",
    "IsADirectoryError",
    "ValidationError",  # pydantic
}


def classify(exc: BaseException) -> ErrorCategory:
    """Best-effort bucket for an exception. Unknown → SERVER_ERROR."""
    name = type(exc).__name__
    if name in _EXTERNAL_EXC_NAMES:
        return ErrorCategory.EXTERNAL_ERROR
    if name in _CLIENT_EXC_NAMES:
        return ErrorCategory.CLIENT_ERROR
    # Walk MRO in case of subclasses with weird names.
    for base in type(exc).__mro__:
        if base.__name__ in _EXTERNAL_EXC_NAMES:
            return ErrorCategory.EXTERNAL_ERROR
        if base.__name__ in _CLIENT_EXC_NAMES:
            return ErrorCategory.CLIENT_ERROR
    return ErrorCategory.SERVER_ERROR
