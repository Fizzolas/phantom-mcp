"""
phantom.tools.vision — screen capture + info.

Legacy had take_screenshot, take_screenshot_hires, get_screen_info.
PR 3 collapses the two screenshot variants into one with a `hires` flag.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from phantom.contracts import ok
from phantom.tools._base import tool


class NoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScreenshotInput(BaseModel):
    region: str = Field(
        "full",
        description="Either 'full' for primary monitor, or 'x,y,width,height'.",
    )
    hires: bool = Field(False, description="Capture at native resolution without downscaling.")
    model_config = ConfigDict(extra="forbid")


@tool("screenshot", category="vision", schema=ScreenshotInput, needs=("display",), timeout_s=15.0)
async def screenshot(region: str = "full", hires: bool = False) -> dict:
    """
    Capture the screen and return it as a base64 PNG.

    `region='full'` captures the entire primary monitor. Otherwise pass
    'x,y,width,height' in pixels. `hires=True` disables downscaling,
    useful when the model needs to read small text.
    """
    from tools.pc_vision import take_screenshot, take_screenshot_hires

    fn = take_screenshot_hires if hires else take_screenshot
    b64 = await fn(region=region)
    return ok({"image_base64": b64, "region": region, "hires": hires})


@tool("screen_info", category="vision", schema=NoArgs, needs=("display",), timeout_s=5.0)
def screen_info() -> dict:
    """Return info about attached displays: count, primary size, DPI."""
    from tools.pc_vision import get_screen_info as legacy

    return ok(legacy())
