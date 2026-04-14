"""
Phantom MCP Server — main entry point.
Runs as an MCP stdio server that LM Studio connects to.
"""

import asyncio
import sys
import json
import logging
from pathlib import Path

# Ensure tools on path
sys.path.insert(0, str(Path(__file__).parent))

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, ImageContent

from tools.pc_vision   import take_screenshot, get_screen_info
from tools.mouse_kb    import (mouse_move, mouse_click, mouse_double_click,
                                mouse_right_click, mouse_scroll,
                                keyboard_type, keyboard_hotkey, keyboard_press)
from tools.shell       import run_cmd, run_powershell, run_persistent_cmd
from tools.file_ops    import (read_file, write_file, append_file,
                                list_dir, delete_file, file_exists,
                                search_files)
from tools.process_ops import list_processes, kill_process, launch_app
from tools.pc_info     import get_pc_snapshot
from tools.window_ops  import (list_windows, focus_window,
                                get_active_window, minimize_window,
                                maximize_window)
from memory.manager    import MemoryManager
from tools.auth_guard  import requires_auth

logging.basicConfig(
    filename=str(Path(__file__).parent / "logs" / "server.log"),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("phantom")

app    = Server("phantom-mcp")
memory = MemoryManager(Path(__file__).parent / "data")

# ── Tool registry ────────────────────────────────────────────────────────────

@app.list_tools()
async def list_tools():
    return [
        # VISION
        Tool(name="screenshot",         description="Capture the current screen. Returns a base64 PNG.", inputSchema={"type":"object","properties":{"region":{"type":"string","description":"'full' or 'x,y,w,h'"}},"required":[]}),
        Tool(name="screen_info",        description="Get screen resolution and monitor count.",           inputSchema={"type":"object","properties":{},"required":[]}),
        # MOUSE
        Tool(name="mouse_move",         description="Move mouse to absolute (x, y).",                    inputSchema={"type":"object","properties":{"x":{"type":"integer"},"y":{"type":"integer"}},"required":["x","y"]}),
        Tool(name="mouse_click",        description="Left-click at (x, y).",                             inputSchema={"type":"object","properties":{"x":{"type":"integer"},"y":{"type":"integer"}},"required":["x","y"]}),
        Tool(name="mouse_double_click", description="Double left-click at (x, y).",                      inputSchema={"type":"object","properties":{"x":{"type":"integer"},"y":{"type":"integer"}},"required":["x","y"]}),
        Tool(name="mouse_right_click",  description="Right-click at (x, y).",                            inputSchema={"type":"object","properties":{"x":{"type":"integer"},"y":{"type":"integer"}},"required":["x","y"]}),
        Tool(name="mouse_scroll",       description="Scroll at (x, y).",                                 inputSchema={"type":"object","properties":{"x":{"type":"integer"},"y":{"type":"integer"},"clicks":{"type":"integer","description":"Positive=up, negative=down"}},"required":["x","y","clicks"]}),
        # KEYBOARD
        Tool(name="keyboard_type",      description="Type a string of text.",                            inputSchema={"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}),
        Tool(name="keyboard_hotkey",    description="Press a key combo like ctrl+c.",                    inputSchema={"type":"object","properties":{"keys":{"type":"string","description":"e.g. ctrl+c, alt+f4"}},"required":["keys"]}),
        Tool(name="keyboard_press",     description="Press a single special key.",                       inputSchema={"type":"object","properties":{"key":{"type":"string","description":"e.g. enter, tab, esc, f5"}},"required":["key"]}),
        # SHELL
        Tool(name="run_cmd",            description="Run a command in CMD. Returns stdout/stderr.",       inputSchema={"type":"object","properties":{"command":{"type":"string"},"timeout":{"type":"integer","default":30}},"required":["command"]}),
        Tool(name="run_powershell",     description="Run a PowerShell command or script.",               inputSchema={"type":"object","properties":{"command":{"type":"string"},"timeout":{"type":"integer","default":30}},"required":["command"]}),
        Tool(name="run_persistent_cmd", description="Run a command in a persistent shell session (retains env/dir).", inputSchema={"type":"object","properties":{"command":{"type":"string"}},"required":["command"]}),
        # FILES
        Tool(name="read_file",          description="Read a file. Returns its text content.",            inputSchema={"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}),
        Tool(name="write_file",         description="Write text to a file. Creates if not exists. USER files require auth.", inputSchema={"type":"object","properties":{"path":{"type":"string"},"content":{"type":"string"}},"required":["path","content"]}),
        Tool(name="append_file",        description="Append text to a file.",                            inputSchema={"type":"object","properties":{"path":{"type":"string"},"content":{"type":"string"}},"required":["path","content"]}),
        Tool(name="list_dir",           description="List files in a directory.",                        inputSchema={"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}),
        Tool(name="delete_file",        description="Delete a file or folder. USER files require auth.", inputSchema={"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}),
        Tool(name="file_exists",        description="Check if a path exists.",                           inputSchema={"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}),
        Tool(name="search_files",       description="Search for files matching a pattern.",              inputSchema={"type":"object","properties":{"root":{"type":"string"},"pattern":{"type":"string"}},"required":["root","pattern"]}),
        # PROCESSES
        Tool(name="list_processes",     description="List running processes with PID and memory.",       inputSchema={"type":"object","properties":{},"required":[]}),
        Tool(name="kill_process",       description="Kill a process by PID. Requires auth for system processes.", inputSchema={"type":"object","properties":{"pid":{"type":"integer"}},"required":["pid"]}),
        Tool(name="launch_app",         description="Launch an application by path or name.",            inputSchema={"type":"object","properties":{"target":{"type":"string"}},"required":["target"]}),
        # PC INFO
        Tool(name="pc_snapshot",        description="Get live PC hardware snapshot (CPU/RAM/GPU/Disk).", inputSchema={"type":"object","properties":{},"required":[]}),
        # WINDOWS
        Tool(name="list_windows",       description="List all open window titles.",                      inputSchema={"type":"object","properties":{},"required":[]}),
        Tool(name="focus_window",       description="Bring a window to focus by title substring.",       inputSchema={"type":"object","properties":{"title":{"type":"string"}},"required":["title"]}),
        Tool(name="get_active_window",  description="Get the title of the currently active window.",     inputSchema={"type":"object","properties":{},"required":[]}),
        Tool(name="minimize_window",    description="Minimize a window by title substring.",             inputSchema={"type":"object","properties":{"title":{"type":"string"}},"required":["title"]}),
        Tool(name="maximize_window",    description="Maximize a window by title substring.",             inputSchema={"type":"object","properties":{"title":{"type":"string"}},"required":["title"]}),
        # MEMORY
        Tool(name="memory_save",        description="Save a memory/note for future sessions.",           inputSchema={"type":"object","properties":{"key":{"type":"string"},"value":{"type":"string"}},"required":["key","value"]}),
        Tool(name="memory_get",         description="Retrieve a stored memory by key.",                  inputSchema={"type":"object","properties":{"key":{"type":"string"}},"required":["key"]}),
        Tool(name="memory_list",        description="List all stored memory keys.",                      inputSchema={"type":"object","properties":{},"required":[]}),
        Tool(name="memory_search",      description="Fuzzy search memories by keyword.",                 inputSchema={"type":"object","properties":{"query":{"type":"string"}},"required":["query"]}),
        Tool(name="memory_compress",    description="Summarize and compress conversation into long-term memory.", inputSchema={"type":"object","properties":{"conversation":{"type":"string"},"label":{"type":"string"}},"required":["conversation","label"]}),
    ]

# ── Tool dispatcher ──────────────────────────────────────────────────────────

@app.call_tool()
async def call_tool(name: str, arguments: dict):
    log.info(f"TOOL CALL: {name} | args={json.dumps(arguments)[:200]}")
    try:
        result = await _dispatch(name, arguments)
        return [TextContent(type="text", text=str(result))]
    except PermissionError as e:
        return [TextContent(type="text", text=f"AUTH_REQUIRED: {e}")]
    except Exception as e:
        log.error(f"Tool error [{name}]: {e}", exc_info=True)
        return [TextContent(type="text", text=f"ERROR: {e}")]

async def _dispatch(name: str, args: dict):
    if name == "screenshot":         return await take_screenshot(args.get("region", "full"))
    if name == "screen_info":        return get_screen_info()
    if name == "mouse_move":         return await mouse_move(args["x"], args["y"])
    if name == "mouse_click":        return await mouse_click(args["x"], args["y"])
    if name == "mouse_double_click": return await mouse_double_click(args["x"], args["y"])
    if name == "mouse_right_click":  return await mouse_right_click(args["x"], args["y"])
    if name == "mouse_scroll":       return await mouse_scroll(args["x"], args["y"], args["clicks"])
    if name == "keyboard_type":      return await keyboard_type(args["text"])
    if name == "keyboard_hotkey":    return await keyboard_hotkey(args["keys"])
    if name == "keyboard_press":     return await keyboard_press(args["key"])
    if name == "run_cmd":            return await run_cmd(args["command"], args.get("timeout", 30))
    if name == "run_powershell":     return await run_powershell(args["command"], args.get("timeout", 30))
    if name == "run_persistent_cmd": return await run_persistent_cmd(args["command"])
    if name == "read_file":          return await read_file(args["path"])
    if name == "write_file":         return await requires_auth(write_file, args["path"], args["content"])
    if name == "append_file":        return await requires_auth(append_file, args["path"], args["content"])
    if name == "list_dir":           return await list_dir(args["path"])
    if name == "delete_file":        return await requires_auth(delete_file, args["path"])
    if name == "file_exists":        return file_exists(args["path"])
    if name == "search_files":       return await search_files(args["root"], args["pattern"])
    if name == "list_processes":     return await list_processes()
    if name == "kill_process":       return await kill_process(args["pid"])
    if name == "launch_app":         return await launch_app(args["target"])
    if name == "pc_snapshot":        return await get_pc_snapshot()
    if name == "list_windows":       return await list_windows()
    if name == "focus_window":       return await focus_window(args["title"])
    if name == "get_active_window":  return get_active_window()
    if name == "minimize_window":    return await minimize_window(args["title"])
    if name == "maximize_window":    return await maximize_window(args["title"])
    if name == "memory_save":        return memory.save(args["key"], args["value"])
    if name == "memory_get":         return memory.get(args["key"])
    if name == "memory_list":        return memory.list_keys()
    if name == "memory_search":      return memory.search(args["query"])
    if name == "memory_compress":    return await memory.compress(args["conversation"], args["label"])
    return f"Unknown tool: {name}"

# ── Entry point ──────────────────────────────────────────────────────────────

async def main():
    # Ensure log/data dirs exist
    (Path(__file__).parent / "logs").mkdir(exist_ok=True)
    (Path(__file__).parent / "data").mkdir(exist_ok=True)
    log.info("Phantom MCP Server starting...")
    # Start tray icon in background
    try:
        from ui.tray import start_tray_thread
        start_tray_thread()
    except Exception:
        pass
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())
