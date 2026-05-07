"""
phantom.tools — registers every tool with the global ToolRegistry.

Modules are imported in dependency order. Each import is wrapped so
that an optional missing dep (pyautogui, mss, win32, pygetwindow,
playwright, tesseract) does NOT crash server boot — the affected tool
just doesn't appear in list_tools.
"""
from phantom.tools._base import tool, registry, ToolRegistry, ToolSpec, _safe_import_tool_module

# Pure-Python / cheap imports first
_safe_import_tool_module("phantom.tools.memory")
_safe_import_tool_module("phantom.tools.cognition")
_safe_import_tool_module("phantom.tools.pc_info")
_safe_import_tool_module("phantom.tools.clipboard")
_safe_import_tool_module("phantom.tools.notify")
_safe_import_tool_module("phantom.tools.files")
_safe_import_tool_module("phantom.tools.shell")
_safe_import_tool_module("phantom.tools.processes")
_safe_import_tool_module("phantom.tools.windows")

# Heavier / desktop-only deps last
_safe_import_tool_module("phantom.tools.desktop")
_safe_import_tool_module("phantom.tools.ocr")
_safe_import_tool_module("phantom.tools.web_search")

__all__ = ["tool", "registry", "ToolRegistry", "ToolSpec"]
