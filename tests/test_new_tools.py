"""
Tests for PR 3 tool migrations (shell, process_ops, file_ops, mouse_kb,
window_ops, vision, web).

Two layers are covered:
  1. Registration — every expected tool appears in the registry with the
     right category + needs.
  2. Schema validation — each tool's pydantic schema rejects bad inputs
     at the registry boundary, before any legacy function runs.

We do not execute desktop-gated tools here (mouse/keyboard/windows) —
those are integration tests that require a display. Schema and
registration coverage is sufficient to catch the two bug classes this
refactor targets: ghost modules and ghost arg names.
"""
from __future__ import annotations

import pytest

import phantom.tools  # noqa: F401 — triggers @tool registrations
from phantom.tools import registry


# ---------------------------------------------------------------------------
# Registration coverage — one assertion per expected tool
# ---------------------------------------------------------------------------


EXPECTED_TOOLS: dict[str, tuple[str, tuple[str, ...]]] = {
    # name: (category, needs)
    # system
    "shell_exec": ("system", ()),
    "list_processes": ("system", ()),
    "find_process": ("system", ()),
    "kill_process": ("system", ()),
    "launch_app": ("system", ("desktop",)),
    # files
    "file_read": ("files", ()),
    "file_write": ("files", ()),
    "file_append": ("files", ()),
    "file_delete": ("files", ()),
    "file_exists": ("files", ()),
    "dir_list": ("files", ()),
    "file_search": ("files", ()),
    "dir_tree": ("files", ()),
    # input
    "mouse_move": ("input", ("desktop",)),
    "mouse_click": ("input", ("desktop",)),
    "mouse_scroll": ("input", ("desktop",)),
    "mouse_drag": ("input", ("desktop",)),
    "keyboard_type": ("input", ("desktop",)),
    "keyboard_key": ("input", ("desktop",)),
    # ui / windows
    "list_windows": ("ui", ("desktop",)),
    "focus_window": ("ui", ("desktop",)),
    "active_window": ("ui", ("desktop",)),
    "window_state": ("ui", ("desktop",)),
    "window_rect": ("ui", ("desktop",)),
    "window_resize": ("ui", ("desktop",)),
    "window_move": ("ui", ("desktop",)),
    # vision
    "screenshot": ("vision", ("display",)),
    "screen_info": ("vision", ("display",)),
    # web
    "search": ("web", ("playwright",)),
    "visit_page": ("web", ("playwright",)),
    "trends": ("web", ("playwright",)),
    "maps": ("web", ("playwright",)),
    "finance": ("web", ("playwright",)),
    "weather": ("web", ("playwright",)),
    "translate": ("web", ("playwright",)),
}


@pytest.mark.parametrize("tool_name,expected", EXPECTED_TOOLS.items())
def test_tool_registered(tool_name: str, expected: tuple[str, tuple[str, ...]]):
    spec = registry.get(tool_name)
    assert spec is not None, f"tool {tool_name!r} not registered"
    expected_category, expected_needs = expected
    assert spec.category == expected_category, (
        f"{tool_name}: category {spec.category!r} != expected {expected_category!r}"
    )
    assert spec.needs == expected_needs, (
        f"{tool_name}: needs {spec.needs!r} != expected {expected_needs!r}"
    )


def test_no_ghost_tools_in_registry():
    """Tools known to be ghost names from legacy must not be registered."""
    ghosts = {
        "run_python", "run_powershell", "run_cmd",  # merged into shell_exec
        "take_screenshot", "take_screenshot_hires",  # merged into screenshot
        "minimize_window", "maximize_window", "restore_window",  # -> window_state
        "mouse_double_click", "mouse_right_click",  # -> mouse_click args
        "keyboard_hotkey",  # -> keyboard_key auto-route
        "stock_price", "crypto_price", "currency_convert",  # ghost names
        "amazon_search", "ebay_search", "youtube_search",  # fake legacy
        "chunk_save", "chunk_load", "chunk_reassemble",  # internal in PR2
    }
    registered = {s.name for s in registry.all()}
    overlap = ghosts & registered
    assert not overlap, f"ghost tools re-registered: {overlap}"


# ---------------------------------------------------------------------------
# Schema validation — each tool rejects obviously bad args
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shell_exec_rejects_unknown_language():
    r = await registry.call("shell_exec", {"language": "ruby", "command": "puts 1"})
    assert r.ok is False
    assert r.meta["category"] == "client_error"


@pytest.mark.asyncio
async def test_shell_exec_rejects_empty_command():
    r = await registry.call("shell_exec", {"language": "shell", "command": ""})
    assert r.ok is False


@pytest.mark.asyncio
async def test_shell_exec_rejects_extreme_timeout():
    r = await registry.call(
        "shell_exec", {"language": "shell", "command": "true", "timeout_s": 9999}
    )
    assert r.ok is False


@pytest.mark.asyncio
async def test_file_write_rejects_empty_path():
    r = await registry.call("file_write", {"path": "", "content": "hi"})
    assert r.ok is False


@pytest.mark.asyncio
async def test_list_processes_rejects_bad_sort():
    r = await registry.call("list_processes", {"sort_by": "sandwich"})
    assert r.ok is False


@pytest.mark.asyncio
async def test_visit_page_rejects_non_http_url():
    r = await registry.call("visit_page", {"url": "file:///etc/passwd"})
    assert r.ok is False


@pytest.mark.asyncio
async def test_search_rejects_unknown_kind():
    r = await registry.call(
        "search", {"query": "cats", "kind": "nonexistent_vertical"}
    )
    assert r.ok is False


@pytest.mark.asyncio
async def test_window_state_rejects_bad_state():
    r = await registry.call(
        "window_state", {"title": "Notepad", "state": "invert"}
    )
    assert r.ok is False


@pytest.mark.asyncio
async def test_screenshot_rejects_extra_args():
    r = await registry.call("screenshot", {"garbage_field": True})
    assert r.ok is False


@pytest.mark.asyncio
async def test_kill_process_accepts_pid_or_name():
    """target: int | str — both shapes should validate, though the actual
    dispatch is gated out unless there's a real process to kill."""
    # With a fake name — validation must pass so we can test the fallback path
    r_name = await registry.call(
        "kill_process", {"target": "nonexistent_process_xyz_phantom"}
    )
    # Either it returns ok=False because the process wasn't found, or
    # because target typing is accepted. Either way NOT a schema error.
    assert r_name.meta.get("category") != "client_error" or \
        "No process matches" in (r_name.error or "")


# ---------------------------------------------------------------------------
# Doc-generator smoke test — ensures the registry is self-describing
# ---------------------------------------------------------------------------


def test_all_tools_have_description():
    missing = [s.name for s in registry.all() if not s.description]
    assert not missing, f"tools without docstrings: {missing}"


def test_all_tools_have_json_schema():
    for s in registry.all():
        js = s.json_schema()
        assert isinstance(js, dict)
        assert js.get("type") == "object", f"{s.name} schema wrong root type"
