"""Unit tests for the ToolRegistry: schema validation + capability gating."""
from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict

from phantom.tools._base import ToolRegistry, tool, registry as global_registry


class EchoInput(BaseModel):
    message: str

    model_config = ConfigDict(extra="forbid")


def _fresh_registry() -> ToolRegistry:
    """Isolated registry for tests — do not touch the module-level singleton."""
    return ToolRegistry()


@pytest.mark.asyncio
async def test_unknown_tool_returns_client_error():
    r = _fresh_registry()
    result = await r.call("nope")
    assert result.ok is False
    assert result.meta["category"] == "client_error"


@pytest.mark.asyncio
async def test_schema_validation_rejects_bad_args():
    reg = _fresh_registry()
    from phantom.tools._base import ToolSpec

    def echo(message: str) -> str:
        return message

    reg.register(
        ToolSpec(
            name="echo",
            fn=echo,
            schema=EchoInput,
            category="test",
            description="echo",
            is_async=False,
        )
    )

    # missing required field
    result = await reg.call("echo", {})
    assert result.ok is False
    assert result.meta["category"] == "client_error"

    # extra/unknown field should fail because extra=forbid
    result = await reg.call("echo", {"message": "hi", "extra": 1})
    assert result.ok is False


@pytest.mark.asyncio
async def test_capability_gating_hides_tool():
    reg = _fresh_registry()
    from phantom.tools._base import ToolSpec

    def ocr_stub() -> str:
        return "text"

    reg.register(
        ToolSpec(
            name="ocr_stub",
            fn=ocr_stub,
            schema=None,
            category="vision",
            description="",
            needs=("tesseract",),
            is_async=False,
        )
    )

    # No capabilities set → tool is not available
    assert "ocr_stub" not in [t.name for t in reg.available()]

    result = await reg.call("ocr_stub", {})
    assert result.ok is False
    assert "tesseract" in result.error

    # After capability is granted, tool becomes available
    reg.set_capabilities({"tesseract"})
    assert "ocr_stub" in [t.name for t in reg.available()]


def test_global_registry_has_proof_tools_registered():
    """Sanity check: after importing phantom.tools, at least the non-gated tools exist."""
    import phantom.tools  # noqa: F401 — triggers registration

    names = {t.name for t in global_registry.all()}
    # system_info has no `needs` so it should always register cleanly
    assert "system_info" in names
    # clipboard_get is pure-Python, should also register
    assert "clipboard_get" in names
