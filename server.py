"""
Phantom MCP Server
Full MCP server for LM Studio. Routes tool calls to PC control modules.
Goal engine runs continuously until task is complete.
File auth guard blocks edits to user files without approval.
"""
import asyncio
import json
import logging
import sys
import traceback
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

# --- project root on sys.path ---
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# --- logging ---
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "server.log", encoding="utf-8"),
        logging.StreamHandler(sys.stderr),
    ],
)
log = logging.getLogger("phantom")

# --- lazy imports (fail gracefully) ---
def _import(module_path: str):
    import importlib
    try:
        return importlib.import_module(module_path)
    except Exception as e:
        log.warning(f"Optional import failed [{module_path}]: {e}")
        return None

# --- memory manager ---
from memory.manager import MemoryManager
mem = MemoryManager(ROOT / "data")

# --- MCP server instance ---
app = Server("phantom-mcp")

# =========================================================
# TOOL DEFINITIONS (schema advertised to LM Studio)
# =========================================================
TOOLS: list[types.Tool] = [
    # --- Vision ---
    types.Tool(
        name="screenshot",
        description="Capture the current screen and return it as a base64 PNG. Use this to see what is on screen before clicking or typing.",
        inputSchema={"type": "object", "properties": {
            "region": {"type": "string", "description": "'full' or 'x,y,width,height'", "default": "full"}
        }}
    ),
    types.Tool(
        name="get_screen_info",
        description="Returns screen resolution so you can calculate valid mouse coordinates.",
        inputSchema={"type": "object", "properties": {}}
    ),
    # --- Mouse ---
    types.Tool(
        name="mouse_move",
        description="Move the mouse cursor to absolute screen coordinates.",
        inputSchema={"type": "object", "required": ["x", "y"], "properties": {
            "x": {"type": "integer"}, "y": {"type": "integer"}
        }}
    ),
    types.Tool(
        name="mouse_click",
        description="Left-click at screen coordinates.",
        inputSchema={"type": "object", "required": ["x", "y"], "properties": {
            "x": {"type": "integer"}, "y": {"type": "integer"}
        }}
    ),
    types.Tool(
        name="mouse_double_click",
        description="Double-click at screen coordinates.",
        inputSchema={"type": "object", "required": ["x", "y"], "properties": {
            "x": {"type": "integer"}, "y": {"type": "integer"}
        }}
    ),
    types.Tool(
        name="mouse_right_click",
        description="Right-click at screen coordinates to open context menus.",
        inputSchema={"type": "object", "required": ["x", "y"], "properties": {
            "x": {"type": "integer"}, "y": {"type": "integer"}
        }}
    ),
    types.Tool(
        name="mouse_scroll",
        description="Scroll the mouse wheel at coordinates. Positive clicks scroll up, negative scroll down.",
        inputSchema={"type": "object", "required": ["x", "y", "clicks"], "properties": {
            "x": {"type": "integer"}, "y": {"type": "integer"},
            "clicks": {"type": "integer", "description": "Positive=up, negative=down"}
        }}
    ),
    # --- Keyboard ---
    types.Tool(
        name="keyboard_type",
        description="Type a string of text as if using the keyboard.",
        inputSchema={"type": "object", "required": ["text"], "properties": {
            "text": {"type": "string"}
        }}
    ),
    types.Tool(
        name="keyboard_hotkey",
        description="Press a keyboard shortcut. Examples: 'ctrl+c', 'alt+f4', 'ctrl+shift+esc', 'win+d'.",
        inputSchema={"type": "object", "required": ["keys"], "properties": {
            "keys": {"type": "string"}
        }}
    ),
    types.Tool(
        name="keyboard_press",
        description="Press a single key. Examples: 'enter', 'escape', 'tab', 'delete', 'f5'.",
        inputSchema={"type": "object", "required": ["key"], "properties": {
            "key": {"type": "string"}
        }}
    ),
    # --- Shell ---
    types.Tool(
        name="run_cmd",
        description="Run a CMD shell command. Returns stdout, stderr, returncode.",
        inputSchema={"type": "object", "required": ["command"], "properties": {
            "command": {"type": "string"},
            "timeout": {"type": "integer", "default": 30}
        }}
    ),
    types.Tool(
        name="run_powershell",
        description="Run a PowerShell command or script block. Supports multi-line scripts.",
        inputSchema={"type": "object", "required": ["command"], "properties": {
            "command": {"type": "string"},
            "timeout": {"type": "integer", "default": 30}
        }}
    ),
    types.Tool(
        name="run_persistent_cmd",
        description="Run a CMD command in a persistent session that remembers the current working directory and environment between calls. Use this for chained operations.",
        inputSchema={"type": "object", "required": ["command"], "properties": {
            "command": {"type": "string"}
        }}
    ),
    # --- Files ---
    types.Tool(
        name="read_file",
        description="Read the full contents of a file. Returns text content.",
        inputSchema={"type": "object", "required": ["path"], "properties": {
            "path": {"type": "string"}
        }}
    ),
    types.Tool(
        name="write_file",
        description="Write content to a file, creating it if needed. Agent-created files are tracked and can be edited freely. Files you did not create will prompt the user for approval.",
        inputSchema={"type": "object", "required": ["path", "content"], "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"}
        }}
    ),
    types.Tool(
        name="append_file",
        description="Append content to the end of an existing file.",
        inputSchema={"type": "object", "required": ["path", "content"], "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"}
        }}
    ),
    types.Tool(
        name="list_dir",
        description="List all files and directories inside a folder.",
        inputSchema={"type": "object", "required": ["path"], "properties": {
            "path": {"type": "string"}
        }}
    ),
    types.Tool(
        name="delete_file",
        description="Delete a file or directory. Requires user approval if the file was not created by the agent.",
        inputSchema={"type": "object", "required": ["path"], "properties": {
            "path": {"type": "string"}
        }}
    ),
    types.Tool(
        name="file_exists",
        description="Check whether a file or directory exists, and whether it was created by the agent.",
        inputSchema={"type": "object", "required": ["path"], "properties": {
            "path": {"type": "string"}
        }}
    ),
    types.Tool(
        name="search_files",
        description="Search for files matching a wildcard pattern under a root directory. Example: search_files('C:/Users/sekri', '*.py')",
        inputSchema={"type": "object", "required": ["root", "pattern"], "properties": {
            "root": {"type": "string"},
            "pattern": {"type": "string"}
        }}
    ),
    # --- Processes ---
    types.Tool(
        name="list_processes",
        description="List the top 50 running processes sorted by RAM usage.",
        inputSchema={"type": "object", "properties": {}}
    ),
    types.Tool(
        name="kill_process",
        description="Terminate a process by PID. System-critical processes are blocked.",
        inputSchema={"type": "object", "required": ["pid"], "properties": {
            "pid": {"type": "integer"}
        }}
    ),
    types.Tool(
        name="launch_app",
        description="Launch an application or open a file. Accepts exe paths, URLs, or document paths.",
        inputSchema={"type": "object", "required": ["target"], "properties": {
            "target": {"type": "string"}
        }}
    ),
    # --- Windows ---
    types.Tool(
        name="list_windows",
        description="List all visible window titles currently open on the desktop.",
        inputSchema={"type": "object", "properties": {}}
    ),
    types.Tool(
        name="focus_window",
        description="Bring a window to the foreground by matching its title (partial match OK).",
        inputSchema={"type": "object", "required": ["title"], "properties": {
            "title": {"type": "string"}
        }}
    ),
    types.Tool(
        name="get_active_window",
        description="Returns the title of the currently focused window.",
        inputSchema={"type": "object", "properties": {}}
    ),
    types.Tool(
        name="minimize_window",
        description="Minimize a window by title.",
        inputSchema={"type": "object", "required": ["title"], "properties": {
            "title": {"type": "string"}
        }}
    ),
    types.Tool(
        name="maximize_window",
        description="Maximize a window by title.",
        inputSchema={"type": "object", "required": ["title"], "properties": {
            "title": {"type": "string"}
        }}
    ),
    # --- PC Info ---
    types.Tool(
        name="get_pc_snapshot",
        description="Returns live CPU%, RAM usage, disk space, GPU VRAM, and network stats. Call this first to understand what resources are available.",
        inputSchema={"type": "object", "properties": {}}
    ),
    # --- Memory ---
    types.Tool(
        name="memory_save",
        description="Save a key-value pair to persistent memory. This survives restarts and can be recalled in future sessions.",
        inputSchema={"type": "object", "required": ["key", "value"], "properties": {
            "key": {"type": "string"},
            "value": {"type": "string"}
        }}
    ),
    types.Tool(
        name="memory_get",
        description="Retrieve a previously saved memory entry by key.",
        inputSchema={"type": "object", "required": ["key"], "properties": {
            "key": {"type": "string"}
        }}
    ),
    types.Tool(
        name="memory_list",
        description="List all keys currently stored in memory.",
        inputSchema={"type": "object", "properties": {}}
    ),
    types.Tool(
        name="memory_search",
        description="Search memory for entries matching a query string.",
        inputSchema={"type": "object", "required": ["query"], "properties": {
            "query": {"type": "string"}
        }}
    ),
    types.Tool(
        name="memory_compress",
        description="Compress and summarize a long conversation string into a compact memory entry using LM Studio.",
        inputSchema={"type": "object", "required": ["conversation", "label"], "properties": {
            "conversation": {"type": "string"},
            "label": {"type": "string"}
        }}
    ),
    # --- Clipboard ---
    types.Tool(
        name="clipboard_get",
        description="Read the current contents of the Windows clipboard.",
        inputSchema={"type": "object", "properties": {}}
    ),
    types.Tool(
        name="clipboard_set",
        description="Write text to the Windows clipboard.",
        inputSchema={"type": "object", "required": ["text"], "properties": {
            "text": {"type": "string"}
        }}
    ),
    # --- Goal engine ---
    types.Tool(
        name="goal_status",
        description="Report whether the current goal is complete or still in progress. Always call this at the end of a work loop to decide whether to continue.",
        inputSchema={"type": "object", "required": ["status", "summary"], "properties": {
            "status": {"type": "string", "enum": ["in_progress", "complete", "blocked"]},
            "summary": {"type": "string", "description": "What was done and what remains."},
            "blocker": {"type": "string", "description": "If blocked, describe what is needed."}
        }}
    ),
]


# =========================================================
# TOOL REGISTRY
# =========================================================
@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return TOOLS


# =========================================================
# TOOL CALL HANDLER
# =========================================================
@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    log.info(f"TOOL CALL: {name} | args={json.dumps(arguments, default=str)[:300]}")
    try:
        result = await _dispatch(name, arguments)
    except PermissionError as e:
        result = {"error": "PERMISSION_DENIED", "message": str(e)}
    except Exception as e:
        log.error(f"Tool {name} raised: {traceback.format_exc()}")
        result = {"error": type(e).__name__, "message": str(e)}
    text = result if isinstance(result, str) else json.dumps(result, indent=2, ensure_ascii=False, default=str)
    log.info(f"TOOL RESULT [{name}]: {text[:200]}")
    return [types.TextContent(type="text", text=text)]


async def _dispatch(name: str, args: dict) -> Any:
    # ---- Vision ----
    if name == "screenshot":
        from tools.pc_vision import take_screenshot
        b64 = await take_screenshot(args.get("region", "full"))
        return {"type": "image/png;base64", "data": b64}

    if name == "get_screen_info":
        from tools.pc_vision import get_screen_info
        return get_screen_info()

    # ---- Mouse ----
    if name == "mouse_move":
        from tools.mouse_kb import mouse_move
        return await mouse_move(args["x"], args["y"])

    if name == "mouse_click":
        from tools.mouse_kb import mouse_click
        return await mouse_click(args["x"], args["y"])

    if name == "mouse_double_click":
        from tools.mouse_kb import mouse_double_click
        return await mouse_double_click(args["x"], args["y"])

    if name == "mouse_right_click":
        from tools.mouse_kb import mouse_right_click
        return await mouse_right_click(args["x"], args["y"])

    if name == "mouse_scroll":
        from tools.mouse_kb import mouse_scroll
        return await mouse_scroll(args["x"], args["y"], args["clicks"])

    # ---- Keyboard ----
    if name == "keyboard_type":
        from tools.mouse_kb import keyboard_type
        return await keyboard_type(args["text"])

    if name == "keyboard_hotkey":
        from tools.mouse_kb import keyboard_hotkey
        return await keyboard_hotkey(args["keys"])

    if name == "keyboard_press":
        from tools.mouse_kb import keyboard_press
        return await keyboard_press(args["key"])

    # ---- Shell ----
    if name == "run_cmd":
        from tools.shell import run_cmd
        return await run_cmd(args["command"], args.get("timeout", 30))

    if name == "run_powershell":
        from tools.shell import run_powershell
        return await run_powershell(args["command"], args.get("timeout", 30))

    if name == "run_persistent_cmd":
        from tools.shell import run_persistent_cmd
        return await run_persistent_cmd(args["command"])

    # ---- Files ----
    if name == "read_file":
        from tools.file_ops import read_file
        return await read_file(args["path"])

    if name == "write_file":
        from tools.auth_guard import requires_auth
        from tools.file_ops import write_file
        return await requires_auth(write_file, args["path"], args["content"])

    if name == "append_file":
        from tools.auth_guard import requires_auth
        from tools.file_ops import append_file
        return await requires_auth(append_file, args["path"], args["content"])

    if name == "list_dir":
        from tools.file_ops import list_dir
        return await list_dir(args["path"])

    if name == "delete_file":
        from tools.auth_guard import requires_auth
        from tools.file_ops import delete_file
        return await requires_auth(delete_file, args["path"])

    if name == "file_exists":
        from tools.file_ops import file_exists
        return file_exists(args["path"])

    if name == "search_files":
        from tools.file_ops import search_files
        return await search_files(args["root"], args["pattern"])

    # ---- Processes ----
    if name == "list_processes":
        from tools.process_ops import list_processes
        return await list_processes()

    if name == "kill_process":
        from tools.process_ops import kill_process
        return await kill_process(args["pid"])

    if name == "launch_app":
        from tools.process_ops import launch_app
        return await launch_app(args["target"])

    # ---- Windows ----
    if name == "list_windows":
        from tools.window_ops import list_windows
        return await list_windows()

    if name == "focus_window":
        from tools.window_ops import focus_window
        return await focus_window(args["title"])

    if name == "get_active_window":
        from tools.window_ops import get_active_window
        return get_active_window()

    if name == "minimize_window":
        from tools.window_ops import minimize_window
        return await minimize_window(args["title"])

    if name == "maximize_window":
        from tools.window_ops import maximize_window
        return await maximize_window(args["title"])

    # ---- PC Info ----
    if name == "get_pc_snapshot":
        from tools.pc_info import get_pc_snapshot
        return await get_pc_snapshot()

    # ---- Memory ----
    if name == "memory_save":
        return mem.save(args["key"], args["value"])

    if name == "memory_get":
        return mem.get(args["key"])

    if name == "memory_list":
        return mem.list_keys()

    if name == "memory_search":
        return mem.search(args["query"])

    if name == "memory_compress":
        return await mem.compress(args["conversation"], args["label"])

    # ---- Clipboard ----
    if name == "clipboard_get":
        from tools.clipboard import clipboard_get
        return clipboard_get()

    if name == "clipboard_set":
        from tools.clipboard import clipboard_set
        return clipboard_set(args["text"])

    # ---- Goal engine ----
    if name == "goal_status":
        status  = args["status"]
        summary = args["summary"]
        blocker = args.get("blocker", "")
        entry   = {"status": status, "summary": summary}
        if blocker:
            entry["blocker"] = blocker
        mem.save("__last_goal_status", json.dumps(entry))
        if status == "blocked":
            log.warning(f"GOAL BLOCKED: {blocker}")
        elif status == "complete":
            log.info(f"GOAL COMPLETE: {summary}")
        return entry

    return {"error": f"Unknown tool: {name}"}


# =========================================================
# ENTRYPOINT
# =========================================================
async def main():
    from ui.tray import start_tray_thread
    start_tray_thread()
    log.info("Phantom MCP server starting on stdio transport...")
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())
