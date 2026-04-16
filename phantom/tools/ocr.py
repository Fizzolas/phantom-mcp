"""
phantom.tools.ocr — screen OCR via Tesseract.

Demonstrates:
  * Schema-enforced argument shape. The legacy OCR tool took a *string*
    "x,y,w,h" region. The new one takes a typed optional object; invalid
    shapes are rejected before any screen grab happens.
  * needs=("tesseract", "display") so the tool is hidden when Tesseract
    isn't installed or the host has no display.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from phantom.contracts import fail, ok
from phantom.tools._base import tool


class Region(BaseModel):
    x: int = Field(..., ge=0)
    y: int = Field(..., ge=0)
    width: int = Field(..., gt=0)
    height: int = Field(..., gt=0)

    model_config = ConfigDict(extra="forbid")


class OCRScreenInput(BaseModel):
    region: Region | None = Field(
        None,
        description="Screen region to capture; omit to OCR the full primary monitor.",
    )
    lang: str = Field("eng", description="Tesseract language code.")

    @field_validator("lang")
    @classmethod
    def _valid_lang(cls, v: str) -> str:
        if not v or not v.replace("+", "").isalpha():
            raise ValueError("lang must be a Tesseract code like 'eng' or 'eng+fra'")
        return v

    model_config = ConfigDict(extra="forbid")


@tool(
    "ocr_screen",
    category="vision",
    schema=OCRScreenInput,
    needs=("tesseract", "display"),
    timeout_s=20.0,
)
async def ocr_screen(region: dict | None = None, lang: str = "eng") -> dict:
    """
    Capture a screen region and run Tesseract OCR on it.

    Use for reading text that the agent cannot access through the DOM or
    accessibility APIs — error dialogs, images of text, embedded screenshots.
    For web pages, prefer visit_page; for UI automation, prefer window/element
    introspection. OCR is the last resort because it is slow and lossy.
    """
    from tools.ocr import ocr_region as legacy_ocr

    region_str = "full"
    if region:
        region_str = f"{region['x']},{region['y']},{region['width']},{region['height']}"

    result = await legacy_ocr(region_str, lang)
    if "error" in result:
        return fail(
            result["error"],
            hint="Ensure Tesseract is installed and a display is available.",
            category="external_error",
        )
    return ok(result)
