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
# TOOL DEFINITIONS
# =========================================================
TOOLS: list[types.Tool] = [
    # --- Vision ---
    types.Tool(
        name="screenshot",
        description="Capture the current screen as a compressed JPEG (1280px max). Use before clicking or typing.",
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
        description="Scroll the mouse wheel at coordinates. Positive=up, negative=down.",
        inputSchema={"type": "object", "required": ["x", "y", "clicks"], "properties": {
            "x": {"type": "integer"}, "y": {"type": "integer"},
            "clicks": {"type": "integer"}
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
        description="Press a keyboard shortcut. Examples: 'ctrl+c', 'alt+f4', 'win+d'.",
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
        description="Run a CMD shell command. Returns stdout, stderr, returncode. Output capped at 8000 chars.",
        inputSchema={"type": "object", "required": ["command"], "properties": {
            "command": {"type": "string"},
            "timeout": {"type": "integer", "default": 30}
        }}
    ),
    types.Tool(
        name="run_powershell",
        description="Run a PowerShell command or script block. Output capped at 8000 chars.",
        inputSchema={"type": "object", "required": ["command"], "properties": {
            "command": {"type": "string"},
            "timeout": {"type": "integer", "default": 30}
        }}
    ),
    types.Tool(
        name="run_persistent_cmd",
        description="Run CMD in a persistent session that remembers cwd between calls. Use for chained operations.",
        inputSchema={"type": "object", "required": ["command"], "properties": {
            "command": {"type": "string"}
        }}
    ),
    # --- Files ---
    types.Tool(
        name="read_file",
        description="Read a file. Output capped at 12000 chars (head+tail). Check 'truncated' field — if true, save to a chunk with memory_chunk_save instead.",
        inputSchema={"type": "object", "required": ["path"], "properties": {
            "path": {"type": "string"}
        }}
    ),
    types.Tool(
        name="write_file",
        description="Write content to a file. Agent-created files are free to edit. User files require approval dialog.",
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
        description="Check whether a file or directory exists.",
        inputSchema={"type": "object", "required": ["path"], "properties": {
            "path": {"type": "string"}
        }}
    ),
    types.Tool(
        name="search_files",
        description="Search for files matching a wildcard pattern under a root directory.",
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
        description="Terminate a process by PID.",
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
        description="Returns live CPU%, RAM, disk, GPU VRAM, and network stats.",
        inputSchema={"type": "object", "properties": {}}
    ),
    # --- Memory: Facts ---
    types.Tool(
        name="memory_save",
        description="Save a named fact to persistent memory. Survives restarts. Use for preferences, project notes, file paths, user info.",
        inputSchema={"type": "object", "required": ["key", "value"], "properties": {
            "key": {"type": "string"},
            "value": {"type": "string"}
        }}
    ),
    types.Tool(
        name="memory_get",
        description="Retrieve a saved memory fact by key.",
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
        description="List all keys currently stored in facts memory.",
        inputSchema={"type": "object", "properties": {}}
    ),
    types.Tool(
        name="memory_search",
        description="Search across all memory namespaces (facts, tasks, chunks) for entries matching a query.",
        inputSchema={"type": "object", "required": ["query"], "properties": {
            "query": {"type": "string"}
        }}
    ),
    types.Tool(
        name="memory_compress",
        description="Intelligently compress a long conversation or text into a compact memory fact using LM Studio. Splits into safe chunks, summarizes each, then merges.",
        inputSchema={"type": "object", "required": ["conversation", "label"], "properties": {
            "conversation": {"type": "string"},
            "label": {"type": "string"}
        }}
    ),
    # --- Memory: Chunks (large content storage) ---
    types.Tool(
        name="memory_chunk_save",
        description=(
            "Split and save large text (code, files, long output) into disk-cached chunks. "
            "Use this instead of memory_save when content is > 4000 chars. "
            "The model can then load chunks one at a time with memory_chunk_load."
        ),
        inputSchema={"type": "object", "required": ["label", "text"], "properties": {
            "label": {"type": "string", "description": "Unique name for this content block, e.g. 'project_main_py'"},
            "text": {"type": "string", "description": "The full text content to chunk and store"}
        }}
    ),
    types.Tool(
        name="memory_chunk_load",
        description=(
            "Load one chunk of a previously saved large content block by label and index. "
            "Check 'has_more' and 'next_index' in the response to know if more chunks exist. "
            "Load chunks one at a time to stay within context limits."
        ),
        inputSchema={"type": "object", "required": ["label", "index"], "properties": {
            "label": {"type": "string"},
            "index": {"type": "integer", "description": "0-based chunk index"}
        }}
    ),
    types.Tool(
        name="memory_chunk_reassemble",
        description="Reassemble all chunks for a label into full text. Only use if you know the full content fits in context (< 20000 chars). Check memory_chunk_list first.",
        inputSchema={"type": "object", "required": ["label"], "properties": {
            "label": {"type": "string"}
        }}
    ),
    types.Tool(
        name="memory_chunk_list",
        description="List all stored chunk labels with their size and chunk count.",
        inputSchema={"type": "object", "properties": {}}
    ),
    types.Tool(
        name="memory_chunk_delete",
        description="Delete all chunks for a given label.",
        inputSchema={"type": "object", "required": ["label"], "properties": {
            "label": {"type": "string"}
        }}
    ),
    # --- Memory: Tasks (long-running work state) ---
    types.Tool(
        name="memory_task_start",
        description=(
            "Create a new task record for a long-running or multi-session goal. "
            "Use this at the start of any complex task so progress is tracked on disk. "
            "task_id should be a short slug, e.g. 'build_flask_api'."
        ),
        inputSchema={"type": "object", "required": ["task_id", "goal"], "properties": {
            "task_id": {"type": "string"},
            "goal": {"type": "string", "description": "Full description of what the task aims to accomplish"}
        }}
    ),
    types.Tool(
        name="memory_task_update",
        description=(
            "Log a step and update the status of an existing task. "
            "Call after each meaningful action to keep a durable progress log. "
            "status: 'in_progress' | 'complete' | 'blocked' | 'failed'."
        ),
        inputSchema={"type": "object", "required": ["task_id", "step"], "properties": {
            "task_id": {"type": "string"},
            "step": {"type": "string", "description": "What was just done"},
            "status": {"type": "string", "enum": ["in_progress", "complete", "blocked", "failed"], "default": "in_progress"},
            "summary": {"type": "string", "description": "Optional updated summary of overall progress"}
        }}
    ),
    types.Tool(
        name="memory_task_load",
        description="Load a task record by task_id. Returns goal, status, all logged steps, and summary.",
        inputSchema={"type": "object", "required": ["task_id"], "properties": {
            "task_id": {"type": "string"}
        }}
    ),
    types.Tool(
        name="memory_task_list",
        description="List all tasks with their current status. Use at session start to resume unfinished work.",
        inputSchema={"type": "object", "properties": {}}
    ),
    # --- Memory: Cache (ephemeral scratch space) ---
    types.Tool(
        name="memory_cache_set",
        description=(
            "Store tool output or scratch data in the ephemeral cache. "
            "Useful for saving shell output, intermediate results, or computed values "
            "that don't need permanent storage. Auto-evicted when cache exceeds 100 entries."
        ),
        inputSchema={"type": "object", "required": ["key", "value"], "properties": {
            "key": {"type": "string"},
            "value": {"type": "string"},
            "ttl": {"type": "integer", "description": "Seconds until expiry. 0 = no expiry.", "default": 0}
        }}
    ),
    types.Tool(
        name="memory_cache_get",
        description="Retrieve a value from the ephemeral cache by key.",
        inputSchema={"type": "object", "required": ["key"], "properties": {
            "key": {"type": "string"}
        }}
    ),
    types.Tool(
        name="memory_cache_list",
        description="List all active (non-expired) cache keys.",
        inputSchema={"type": "object", "properties": {}}
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
        description="Report whether the current goal is complete or still in progress. Always call at the end of a work loop.",
        inputSchema={"type": "object", "required": ["status", "summary"], "properties": {
            "status": {"type": "string", "enum": ["in_progress", "complete", "blocked"]},
            "summary": {"type": "string"},
            "blocker": {"type": "string"}
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
        return {"type": "image/jpeg;base64", "data": b64}

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

    # ---- Memory: Facts ----
    if name == "memory_save":
        return mem.save(args["key"], args["value"])

    if name == "memory_get":
        return mem.get(args["key"])

    if name == "memory_delete":
        return mem.delete(args["key"])

    if name == "memory_list":
        return mem.list_keys()

    if name == "memory_search":
        return mem.search(args["query"])

    if name == "memory_compress":
        return await mem.compress(args["conversation"], args["label"])

    # ---- Memory: Chunks ----
    if name == "memory_chunk_save":
        return mem.chunk_save(args["label"], args["text"])

    if name == "memory_chunk_load":
        return mem.chunk_load(args["label"], args["index"])

    if name == "memory_chunk_reassemble":
        return mem.chunk_reassemble(args["label"])

    if name == "memory_chunk_list":
        return mem.chunk_list()

    if name == "memory_chunk_delete":
        return mem.chunk_delete(args["label"])

    # ---- Memory: Tasks ----
    if name == "memory_task_start":
        return mem.task_start(args["task_id"], args["goal"])

    if name == "memory_task_update":
        return mem.task_update(
            args["task_id"],
            args["step"],
            args.get("status", "in_progress"),
            args.get("summary", ""),
        )

    if name == "memory_task_load":
        return mem.task_load(args["task_id"])

    if name == "memory_task_list":
        return mem.task_list()

    # ---- Memory: Cache ----
    if name == "memory_cache_set":
        return mem.cache_set(args["key"], args["value"], args.get("ttl", 0))

    if name == "memory_cache_get":
        return mem.cache_get(args["key"])

    if name == "memory_cache_list":
        return mem.cache_list()

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
