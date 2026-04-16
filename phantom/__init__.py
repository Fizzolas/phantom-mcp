"""
phantom — the refactored core of phantom-mcp.

This package is being introduced incrementally alongside the legacy
server.py + tools/ + memory/ tree. PR 1 adds the foundation only:

  phantom.contracts   — ToolResult envelope + structured errors
  phantom.runtime     — safe executor, LM Studio probe, token budget
  phantom.tools._base — @tool decorator + ToolRegistry

Nothing here is wired into server.py yet. Later PRs will migrate tools
one category at a time and finally replace the monolithic dispatch.
"""

__version__ = "0.1.0-pr1"
