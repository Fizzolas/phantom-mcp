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
# TOOL DEFINITIONS
# =========================================================
TOOLS: list[types.Tool] = [

    # === VISION ===
    types.Tool(
        name="screenshot",
        description=(
            "Capture the current screen as a compressed JPEG (max 1280px wide). "
            "Use before any click or UI interaction to confirm element positions. "
            "Use region='x,y,w,h' to zoom into a specific area for small text."
        ),
        inputSchema={"type": "object", "properties": {
            "region": {
                "type": "string",
                "description": "'full' for full screen, or 'x,y,width,height' to capture a sub-region",
                "default": "full"
            }
        }}
    ),
    types.Tool(
        name="get_screen_info",
        description="Returns screen resolution and screenshot compression settings. Call once per session to know coordinate bounds.",
        inputSchema={"type": "object", "properties": {}}
    ),

    # === MOUSE ===
    types.Tool(
        name="mouse_move",
        description="Move the mouse cursor to absolute screen coordinates without clicking.",
        inputSchema={"type": "object", "required": ["x", "y"], "properties": {
            "x": {"type": "integer", "description": "Horizontal pixel coordinate"},
            "y": {"type": "integer", "description": "Vertical pixel coordinate"},
            "duration": {"type": "number", "description": "Seconds for the move animation (default 0.15)", "default": 0.15}
        }}
    ),
    types.Tool(
        name="mouse_click",
        description="Click at screen coordinates. Supports left/right/middle buttons and double-click.",
        inputSchema={"type": "object", "required": ["x", "y"], "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
            "clicks": {"type": "integer", "description": "1 = single click, 2 = double click", "default": 1}
        }}
    ),
    types.Tool(
        name="mouse_double_click",
        description="Double-click at screen coordinates (shorthand for mouse_click with clicks=2).",
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
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "clicks": {"type": "integer", "description": "Positive=up, negative=down. Typical range: -10 to 10"}
        }}
    ),
    types.Tool(
        name="mouse_drag",
        description=(
            "Click and drag from one position to another. "
            "Use for moving windows, selecting text ranges, slider controls, or drag-and-drop."
        ),
        inputSchema={"type": "object", "required": ["x1", "y1", "x2", "y2"], "properties": {
            "x1": {"type": "integer", "description": "Start X"},
            "y1": {"type": "integer", "description": "Start Y"},
            "x2": {"type": "integer", "description": "End X"},
            "y2": {"type": "integer", "description": "End Y"},
            "duration": {"type": "number", "description": "Seconds for drag (default 0.4)", "default": 0.4},
            "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"}
        }}
    ),

    # === KEYBOARD ===
    types.Tool(
        name="keyboard_type",
        description=(
            "Type text. Automatically uses clipboard-paste for special characters "
            "(newlines, @, #, {}, etc.) so nothing is dropped. "
            "Use for filling forms, search boxes, terminals."
        ),
        inputSchema={"type": "object", "required": ["text"], "properties": {
            "text": {"type": "string"},
            "interval": {"type": "number", "description": "Seconds between keystrokes for direct-type path (default 0.02)", "default": 0.02}
        }}
    ),
    types.Tool(
        name="keyboard_hotkey",
        description="Press a keyboard shortcut. Join keys with '+'. Examples: 'ctrl+c', 'alt+f4', 'ctrl+shift+esc', 'win+d', 'ctrl+alt+delete'.",
        inputSchema={"type": "object", "required": ["keys"], "properties": {
            "keys": {"type": "string", "description": "Keys joined by '+', e.g. 'ctrl+s'"}
        }}
    ),
    types.Tool(
        name="keyboard_press",
        description="Press a single key one or more times. Examples: 'enter', 'escape', 'tab', 'delete', 'f5', 'space', 'backspace'.",
        inputSchema={"type": "object", "required": ["key"], "properties": {
            "key": {"type": "string"},
            "presses": {"type": "integer", "description": "How many times to press (default 1)", "default": 1}
        }}
    ),
    types.Tool(
        name="keyboard_key_down",
        description="Hold a key down without releasing. Pair with keyboard_key_up. Use for shift+click, ctrl+drag, etc.",
        inputSchema={"type": "object", "required": ["key"], "properties": {
            "key": {"type": "string", "description": "Key to hold, e.g. 'shift', 'ctrl', 'alt'"}
        }}
    ),
    types.Tool(
        name="keyboard_key_up",
        description="Release a key that was held down with keyboard_key_down.",
        inputSchema={"type": "object", "required": ["key"], "properties": {
            "key": {"type": "string"}
        }}
    ),

    # === SHELL ===
    types.Tool(
        name="run_cmd",
        description="Run a CMD shell command. Returns stdout, stderr, returncode. Output capped at 8000 chars.",
        inputSchema={"type": "object", "required": ["command"], "properties": {
            "command": {"type": "string"},
            "timeout": {"type": "integer", "description": "Max seconds to wait (default 30)", "default": 30}
        }}
    ),
    types.Tool(
        name="run_powershell",
        description=(
            "Run a PowerShell command or multi-line script. Output capped at 8000 chars. "
            "Prefer this over run_cmd for complex operations, file parsing, and registry access."
        ),
        inputSchema={"type": "object", "required": ["command"], "properties": {
            "command": {"type": "string"},
            "timeout": {"type": "integer", "default": 30}
        }}
    ),
    types.Tool(
        name="run_persistent_cmd",
        description=(
            "Run CMD in a persistent session that remembers cwd and env between calls. "
            "Use this for chained operations like: cd to a directory, then run commands in it."
        ),
        inputSchema={"type": "object", "required": ["command"], "properties": {
            "command": {"type": "string"},
            "timeout": {"type": "integer", "default": 30}
        }}
    ),

    # === FILES ===
    types.Tool(
        name="read_file",
        description=(
            "Read a file's contents. Output capped at 12000 chars (shows head+tail if truncated). "
            "If 'truncated' is true in the response, use memory_chunk_save to store it in chunks instead."
        ),
        inputSchema={"type": "object", "required": ["path"], "properties": {
            "path": {"type": "string", "description": "Absolute or relative file path"}
        }}
    ),
    types.Tool(
        name="write_file",
        description="Write content to a file. Creates parent directories if needed. Agent-created files are edited freely; user files need approval.",
        inputSchema={"type": "object", "required": ["path", "content"], "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"}
        }}
    ),
    types.Tool(
        name="append_file",
        description="Append text to the end of a file without overwriting it.",
        inputSchema={"type": "object", "required": ["path", "content"], "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"}
        }}
    ),
    types.Tool(
        name="list_dir",
        description="List files and directories in a folder with name, type, and size.",
        inputSchema={"type": "object", "required": ["path"], "properties": {
            "path": {"type": "string"}
        }}
    ),
    types.Tool(
        name="delete_file",
        description="Delete a file or directory tree. User-owned files require an approval dialog.",
        inputSchema={"type": "object", "required": ["path"], "properties": {
            "path": {"type": "string"}
        }}
    ),
    types.Tool(
        name="file_exists",
        description="Check whether a path exists and whether it is a file or directory.",
        inputSchema={"type": "object", "required": ["path"], "properties": {
            "path": {"type": "string"}
        }}
    ),
    types.Tool(
        name="search_files",
        description="Search for files matching a glob pattern under a root directory. Returns up to 200 matches.",
        inputSchema={"type": "object", "required": ["root", "pattern"], "properties": {
            "root": {"type": "string", "description": "Directory to search from"},
            "pattern": {"type": "string", "description": "Glob pattern, e.g. '*.py', '**/*.json', 'config*'"}
        }}
    ),

    # === PROCESSES ===
    types.Tool(
        name="list_processes",
        description="List running processes with PID, name, RAM MB, CPU%, and status.",
        inputSchema={"type": "object", "properties": {
            "sort_by": {"type": "string", "enum": ["ram", "cpu", "name", "pid"], "default": "ram"},
            "limit": {"type": "integer", "description": "Max results (1-200, default 50)", "default": 50}
        }}
    ),
    types.Tool(
        name="find_process",
        description="Find all processes whose name contains a search string. Returns PID, exe path, RAM, CPU, status.",
        inputSchema={"type": "object", "required": ["name"], "properties": {
            "name": {"type": "string", "description": "Partial name to search, e.g. 'chrome', 'python', 'lmstudio'"}
        }}
    ),
    types.Tool(
        name="kill_process",
        description=(
            "Terminate a process by PID. "
            "Set force=true to SIGKILL processes that ignore normal termination. "
            "System-critical processes (lsass, csrss, etc.) are always blocked."
        ),
        inputSchema={"type": "object", "required": ["pid"], "properties": {
            "pid": {"type": "integer"},
            "force": {"type": "boolean", "description": "Use SIGKILL instead of SIGTERM", "default": False}
        }}
    ),
    types.Tool(
        name="launch_app",
        description="Launch an application, open a URL, or open a document. Returns PID of launched process.",
        inputSchema={"type": "object", "required": ["target"], "properties": {
            "target": {"type": "string", "description": "Exe path, URL, or document path"},
            "wait": {"type": "boolean", "description": "Wait for process to start before returning", "default": False},
            "timeout": {"type": "integer", "description": "Seconds to wait if wait=true (default 10)", "default": 10}
        }}
    ),

    # === WINDOWS ===
    types.Tool(
        name="list_windows",
        description="List all visible windows with title, position (left/top), size (width/height), and minimized/active state.",
        inputSchema={"type": "object", "properties": {}}
    ),
    types.Tool(
        name="focus_window",
        description="Bring a window to the foreground. Restores it from minimized state first if needed. Partial title match.",
        inputSchema={"type": "object", "required": ["title"], "properties": {
            "title": {"type": "string", "description": "Partial window title to match"}
        }}
    ),
    types.Tool(
        name="get_active_window",
        description="Returns the title, position, and size of the currently focused window.",
        inputSchema={"type": "object", "properties": {}}
    ),
    types.Tool(
        name="get_window_rect",
        description="Get the exact position (left, top), size (width, height), and center coordinates of a window by title.",
        inputSchema={"type": "object", "required": ["title"], "properties": {
            "title": {"type": "string"}
        }}
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
    types.Tool(
        name="restore_window",
        description="Restore a minimized or maximized window to its normal size.",
        inputSchema={"type": "object", "required": ["title"], "properties": {
            "title": {"type": "string"}
        }}
    ),
    types.Tool(
        name="resize_window",
        description="Resize a window to specific pixel dimensions.",
        inputSchema={"type": "object", "required": ["title", "width", "height"], "properties": {
            "title": {"type": "string"},
            "width": {"type": "integer"},
            "height": {"type": "integer"}
        }}
    ),
    types.Tool(
        name="move_window",
        description="Move a window's top-left corner to absolute screen coordinates.",
        inputSchema={"type": "object", "required": ["title", "x", "y"], "properties": {
            "title": {"type": "string"},
            "x": {"type": "integer"},
            "y": {"type": "integer"}
        }}
    ),

    # === PC INFO ===
    types.Tool(
        name="get_pc_snapshot",
        description="Returns live CPU% (total + per-core), RAM, swap, all disk drives, GPU VRAM/temp/load, and network IO. Call at session start.",
        inputSchema={"type": "object", "properties": {}}
    ),

    # === MEMORY: FACTS ===
    types.Tool(
        name="memory_save",
        description="Save a named fact to persistent memory. Survives server restarts. Use for preferences, paths, configs, user info.",
        inputSchema={"type": "object", "required": ["key", "value"], "properties": {
            "key": {"type": "string"},
            "value": {"type": "string"}
        }}
    ),
    types.Tool(
        name="memory_get",
        description="Retrieve a saved memory fact by exact key name.",
        inputSchema={"type": "object", "required": ["key"], "properties": {
            "key": {"type": "string"}
        }}
    ),
    types.Tool(
        name="memory_delete",
        description="Delete a memory fact by key.",
        inputSchema={"type": "object", "required": ["key"], "properties": {
            "key": {"type": "string"}
        }}
    ),
    types.Tool(
        name="memory_list",
        description="List all keys in facts memory.",
        inputSchema={"type": "object", "properties": {}}
    ),
    types.Tool(
        name="memory_search",
        description="Fuzzy search across all memory namespaces (facts, tasks, chunk labels). Returns top 15 matches with scores.",
        inputSchema={"type": "object", "required": ["query"], "properties": {
            "query": {"type": "string"}
        }}
    ),
    types.Tool(
        name="memory_compress",
        description=(
            "Compress a long conversation or text into a compact memory fact using LM Studio. "
            "Splits into safe chunks, summarizes each, then merges into one digest. "
            "Use when conversation context grows long to free up token space."
        ),
        inputSchema={"type": "object", "required": ["conversation", "label"], "properties": {
            "conversation": {"type": "string"},
            "label": {"type": "string", "description": "Short name for this memory, e.g. 'session_2026_04_14'"}
        }}
    ),

    # === MEMORY: CHUNKS ===
    types.Tool(
        name="memory_chunk_save",
        description=(
            "Split and store large text (code, files, long output) as numbered disk chunks. "
            "Each chunk is ~6000 chars (~1700 tokens). Use instead of memory_save for content > 4000 chars. "
            "Returns chunk count so you know how many pieces to load."
        ),
        inputSchema={"type": "object", "required": ["label", "text"], "properties": {
            "label": {"type": "string", "description": "Unique name, e.g. 'main_py', 'task_output_v2'"},
            "text": {"type": "string"}
        }}
    ),
    types.Tool(
        name="memory_chunk_load",
        description=(
            "Load one chunk of a stored content block by label and index. "
            "Check 'has_more' and 'next_index' to iterate. Load one chunk at a time."
        ),
        inputSchema={"type": "object", "required": ["label", "index"], "properties": {
            "label": {"type": "string"},
            "index": {"type": "integer", "description": "0-based index"}
        }}
    ),
    types.Tool(
        name="memory_chunk_reassemble",
        description="Reassemble all chunks for a label into full text. Only use if total_chars < 20000 (check memory_chunk_list first).",
        inputSchema={"type": "object", "required": ["label"], "properties": {
            "label": {"type": "string"}
        }}
    ),
    types.Tool(
        name="memory_chunk_list",
        description="List all stored chunk labels with total_chars and chunk count.",
        inputSchema={"type": "object", "properties": {}}
    ),
    types.Tool(
        name="memory_chunk_delete",
        description="Delete all chunks and manifest for a given label.",
        inputSchema={"type": "object", "required": ["label"], "properties": {
            "label": {"type": "string"}
        }}
    ),

    # === MEMORY: TASKS ===
    types.Tool(
        name="memory_task_start",
        description=(
            "Create a task record for a long or multi-session goal. "
            "Call at the start of any complex task. Use a short slug as task_id."
        ),
        inputSchema={"type": "object", "required": ["task_id", "goal"], "properties": {
            "task_id": {"type": "string", "description": "Short slug, e.g. 'build_flask_api'"},
            "goal":    {"type": "string", "description": "Full description of what needs to be done"}
        }}
    ),
    types.Tool(
        name="memory_task_update",
        description="Log a step and update task status. Call after each meaningful action.",
        inputSchema={"type": "object", "required": ["task_id", "step"], "properties": {
            "task_id": {"type": "string"},
            "step":    {"type": "string", "description": "What was just accomplished"},
            "status":  {"type": "string", "enum": ["in_progress", "complete", "blocked", "failed"], "default": "in_progress"},
            "summary": {"type": "string", "description": "Optional updated summary of overall progress"}
        }}
    ),
    types.Tool(
        name="memory_task_load",
        description="Load a full task record by task_id: goal, status, all logged steps, summary.",
        inputSchema={"type": "object", "required": ["task_id"], "properties": {
            "task_id": {"type": "string"}
        }}
    ),
    types.Tool(
        name="memory_task_list",
        description="List all tasks with status and step count. Call at session start to find unfinished work.",
        inputSchema={"type": "object", "properties": {}}
    ),

    # === MEMORY: CACHE ===
    types.Tool(
        name="memory_cache_set",
        description="Store ephemeral data (shell output, intermediate results) in a keyed cache. Auto-evicted at 100 entries.",
        inputSchema={"type": "object", "required": ["key", "value"], "properties": {
            "key":   {"type": "string"},
            "value": {"type": "string"},
            "ttl":   {"type": "integer", "description": "Seconds until expiry. 0 = no expiry", "default": 0}
        }}
    ),
    types.Tool(
        name="memory_cache_get",
        description="Retrieve a cached value by key. Returns CACHE MISS if expired or not found.",
        inputSchema={"type": "object", "required": ["key"], "properties": {
            "key": {"type": "string"}
        }}
    ),
    types.Tool(
        name="memory_cache_list",
        description="List all active (non-expired) cache keys with size and expiry info.",
        inputSchema={"type": "object", "properties": {}}
    ),

    # === CLIPBOARD ===
    types.Tool(
        name="clipboard_get",
        description="Read the current contents of the Windows clipboard.",
        inputSchema={"type": "object", "properties": {}}
    ),
    types.Tool(
        name="clipboard_set",
        description="Write text to the Windows clipboard. Useful before keyboard_hotkey('ctrl+v') to paste large text.",
        inputSchema={"type": "object", "required": ["text"], "properties": {
            "text": {"type": "string"}
        }}
    ),

    # === GOAL ENGINE ===
    types.Tool(
        name="goal_status",
        description=(
            "Report goal progress. Always call at the end of each work loop iteration. "
            "'in_progress' = keep working. 'complete' = goal verified done. "
            "'blocked' = need user input (describe in blocker field)."
        ),
        inputSchema={"type": "object", "required": ["status", "summary"], "properties": {
            "status":  {"type": "string", "enum": ["in_progress", "complete", "blocked"]},
            "summary": {"type": "string", "description": "What was done and what remains"},
            "blocker": {"type": "string", "description": "If blocked: what is needed from the user"}
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
        log.error(f"Tool {name} raised:\n{traceback.format_exc()}")
        result = {"error": type(e).__name__, "message": str(e)}
    text = result if isinstance(result, str) else json.dumps(result, indent=2, ensure_ascii=False, default=str)
    log.info(f"TOOL RESULT [{name}]: {text[:200]}")
    return [types.TextContent(type="text", text=text)]


async def _dispatch(name: str, args: dict) -> Any:

    # === VISION ===
    if name == "screenshot":
        from tools.pc_vision import take_screenshot
        b64 = await take_screenshot(args.get("region", "full"))
        return {"type": "image/jpeg;base64", "data": b64}

    if name == "get_screen_info":
        from tools.pc_vision import get_screen_info
        return get_screen_info()

    # === MOUSE ===
    if name == "mouse_move":
        from tools.mouse_kb import mouse_move
        return await mouse_move(args["x"], args["y"], args.get("duration", 0.15))

    if name == "mouse_click":
        from tools.mouse_kb import mouse_click
        return await mouse_click(args["x"], args["y"], args.get("button", "left"), args.get("clicks", 1))

    if name == "mouse_double_click":
        from tools.mouse_kb import mouse_double_click
        return await mouse_double_click(args["x"], args["y"])

    if name == "mouse_right_click":
        from tools.mouse_kb import mouse_right_click
        return await mouse_right_click(args["x"], args["y"])

    if name == "mouse_scroll":
        from tools.mouse_kb import mouse_scroll
        return await mouse_scroll(args["x"], args["y"], args["clicks"])

    if name == "mouse_drag":
        from tools.mouse_kb import mouse_drag
        return await mouse_drag(
            args["x1"], args["y1"], args["x2"], args["y2"],
            args.get("duration", 0.4), args.get("button", "left")
        )

    # === KEYBOARD ===
    if name == "keyboard_type":
        from tools.mouse_kb import keyboard_type
        return await keyboard_type(args["text"], args.get("interval", 0.02))

    if name == "keyboard_hotkey":
        from tools.mouse_kb import keyboard_hotkey
        return await keyboard_hotkey(args["keys"])

    if name == "keyboard_press":
        from tools.mouse_kb import keyboard_press
        return await keyboard_press(args["key"], args.get("presses", 1))

    if name == "keyboard_key_down":
        from tools.mouse_kb import keyboard_key_down
        return await keyboard_key_down(args["key"])

    if name == "keyboard_key_up":
        from tools.mouse_kb import keyboard_key_up
        return await keyboard_key_up(args["key"])

    # === SHELL ===
    if name == "run_cmd":
        from tools.shell import run_cmd
        return await run_cmd(args["command"], args.get("timeout", 30))

    if name == "run_powershell":
        from tools.shell import run_powershell
        return await run_powershell(args["command"], args.get("timeout", 30))

    if name == "run_persistent_cmd":
        from tools.shell import run_persistent_cmd
        return await run_persistent_cmd(args["command"])

    # === FILES ===
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

    # === PROCESSES ===
    if name == "list_processes":
        from tools.process_ops import list_processes
        return await list_processes(args.get("sort_by", "ram"), args.get("limit", 50))

    if name == "find_process":
        from tools.process_ops import find_process
        return await find_process(args["name"])

    if name == "kill_process":
        from tools.process_ops import kill_process
        return await kill_process(args["pid"], args.get("force", False))

    if name == "launch_app":
        from tools.process_ops import launch_app
        return await launch_app(args["target"], args.get("wait", False), args.get("timeout", 10))

    # === WINDOWS ===
    if name == "list_windows":
        from tools.window_ops import list_windows
        return await list_windows()

    if name == "focus_window":
        from tools.window_ops import focus_window
        return await focus_window(args["title"])

    if name == "get_active_window":
        from tools.window_ops import get_active_window
        return get_active_window()

    if name == "get_window_rect":
        from tools.window_ops import get_window_rect
        return await get_window_rect(args["title"])

    if name == "minimize_window":
        from tools.window_ops import minimize_window
        return await minimize_window(args["title"])

    if name == "maximize_window":
        from tools.window_ops import maximize_window
        return await maximize_window(args["title"])

    if name == "restore_window":
        from tools.window_ops import restore_window
        return await restore_window(args["title"])

    if name == "resize_window":
        from tools.window_ops import resize_window
        return await resize_window(args["title"], args["width"], args["height"])

    if name == "move_window":
        from tools.window_ops import move_window
        return await move_window(args["title"], args["x"], args["y"])

    # === PC INFO ===
    if name == "get_pc_snapshot":
        from tools.pc_info import get_pc_snapshot
        return await get_pc_snapshot()

    # === MEMORY: FACTS ===
    if name == "memory_save":     return mem.save(args["key"], args["value"])
    if name == "memory_get":      return mem.get(args["key"])
    if name == "memory_delete":   return mem.delete(args["key"])
    if name == "memory_list":     return mem.list_keys()
    if name == "memory_search":   return mem.search(args["query"])
    if name == "memory_compress": return await mem.compress(args["conversation"], args["label"])

    # === MEMORY: CHUNKS ===
    if name == "memory_chunk_save":       return mem.chunk_save(args["label"], args["text"])
    if name == "memory_chunk_load":       return mem.chunk_load(args["label"], args["index"])
    if name == "memory_chunk_reassemble": return mem.chunk_reassemble(args["label"])
    if name == "memory_chunk_list":       return mem.chunk_list()
    if name == "memory_chunk_delete":     return mem.chunk_delete(args["label"])

    # === MEMORY: TASKS ===
    if name == "memory_task_start":  return mem.task_start(args["task_id"], args["goal"])
    if name == "memory_task_update": return mem.task_update(
        args["task_id"], args["step"],
        args.get("status", "in_progress"), args.get("summary", "")
    )
    if name == "memory_task_load":   return mem.task_load(args["task_id"])
    if name == "memory_task_list":   return mem.task_list()

    # === MEMORY: CACHE ===
    if name == "memory_cache_set":  return mem.cache_set(args["key"], args["value"], args.get("ttl", 0))
    if name == "memory_cache_get":  return mem.cache_get(args["key"])
    if name == "memory_cache_list": return mem.cache_list()

    # === CLIPBOARD ===
    if name == "clipboard_get":
        from tools.clipboard import clipboard_get
        return clipboard_get()

    if name == "clipboard_set":
        from tools.clipboard import clipboard_set
        return clipboard_set(args["text"])

    # === GOAL ENGINE ===
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

    return {"error": f"Unknown tool: '{name}'"}


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
