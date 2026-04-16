"""
phantom.tools — new-style tools that register via the @tool decorator.

This package coexists with the legacy phantom-mcp tools/ directory
during the migration. See phantom.tools._base for the decorator.
"""
from phantom.tools._base import tool, registry, ToolRegistry, ToolSpec

# Import submodules so their @tool decorators fire on package import.
# Keep this list ordered: cheap, pure-Python imports first; heavy deps last.
# Each import is wrapped so a missing optional dep DOES NOT crash the server.
from phantom.tools._base import _safe_import_tool_module

# PR 1 proof tools
_safe_import_tool_module("phantom.tools.pc_info")
_safe_import_tool_module("phantom.tools.clipboard")
_safe_import_tool_module("phantom.tools.notify")
_safe_import_tool_module("phantom.tools.ocr")
_safe_import_tool_module("phantom.tools.web_search")

# PR 2 memory + task tools
_safe_import_tool_module("phantom.tools.memory")

# PR 3 system / files / input / ui / web tools
_safe_import_tool_module("phantom.tools.shell")
_safe_import_tool_module("phantom.tools.process_ops")
_safe_import_tool_module("phantom.tools.file_ops")
_safe_import_tool_module("phantom.tools.mouse_kb")
_safe_import_tool_module("phantom.tools.window_ops")
_safe_import_tool_module("phantom.tools.vision")
_safe_import_tool_module("phantom.tools.web")

__all__ = ["tool", "registry", "ToolRegistry", "ToolSpec"]
