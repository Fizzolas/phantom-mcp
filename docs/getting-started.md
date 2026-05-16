# Phantom MCP — Getting Started

This guide is written for someone who is comfortable using their PC and
running apps, but does not assume Python or MCP background knowledge.
If you get stuck on any step, copy the error and ask your AI assistant
to help.

---

## What this server actually does

Phantom-MCP is a small program that runs on your PC and exposes a set
of "tools" to the language model loaded in LM Studio or Jan.ai. The model can
call these tools to take screenshots, click, type, run shell commands,
open files, focus windows, remember things between sessions, and so on.

**You still talk to the model through the chat box.** Phantom
is not a chat UI — it is the model's hands.

---

## Two big rules to know up front

1. **Context windows are finite.** Every tool result has to fit inside
   the model's context window. Phantom enforces this automatically: big
   outputs get trimmed with a sentinel message so the model knows there
   was more. Bigger context (16k, 32k+) gives the model more room to
   remember things; smaller context (4k–8k) still works but the model
   forgets older steps faster.

2. **Config files launch MCP servers — nothing else.** Your model name,
   context length, GPU offload, etc. live inside LM Studio's (or Jan.ai's)
   own settings. Don't try to put them in the MCP config. The example
   configs in this repo only contain the launch command.

---

## Setup, step by step

### 1. Get the files onto your PC

```bat
git clone https://github.com/Fizzolas/phantom-mcp C:\phantom-mcp
cd C:\phantom-mcp
```

### 2. Make sure Python works

If you're on Windows and have never used Python from the command line,
the easiest sanity check is:

```bat
py -3 --version
```

You should see `Python 3.11.x` or higher. If `py` is not found:

- Install Python from <https://www.python.org/downloads/windows/>.
- During install, **check "Add python.exe to PATH"**.
- Open a NEW command prompt (the PATH update doesn't apply to the open one).

### 3. Run the installer

Double-click **`install.bat`** in the `C:\phantom-mcp` folder, or run:

```bat
C:\phantom-mcp\install.bat
```

This will:
- Check your Python version is 3.8 or higher
- Create a virtual environment (`.venv`) inside the folder
- Install all required Python packages
- Create the `data\phantom_memory` folder Phantom writes to
- Tell you if any optional tools (like Tesseract-OCR for screen text reading) are missing

If you see a red error during install, read the message — it will tell you exactly what
to install or fix. Then run `install.bat` again.

> **Tip:** You only need to run `install.bat` once. After that, use `launch.bat` to start the server.

### 4. Tell your AI host about the server

#### LM Studio

1. Open LM Studio.
2. Go to **Settings → MCP Servers → Add Server (Manual / stdio)**.
3. Use these values:
   - **Command:** `C:\phantom-mcp\.venv\Scripts\python.exe`
   - **Args:** `C:\phantom-mcp\server_v2.py`
   - **Working directory:** `C:\phantom-mcp`
4. Save. Restart LM Studio.

Or import the ready-made JSON from [`lmstudio_config.json`](../lmstudio_config.json).

> **Why the full path?** Phantom uses a virtual environment with its own copy of Python.
> If you type just `python`, LM Studio might launch the wrong Python and fail to import
> the packages Phantom needs.

#### Jan.ai

1. Open Jan.
2. Go to **Settings → Model Context Protocol → Edit** (the pencil icon on the config).
3. Add the following to the JSON object:

```json
"PhantomMCP": {
  "active": true,
  "command": "C:\\phantom-mcp\\.venv\\Scripts\\python.exe",
  "args": ["C:\\phantom-mcp\\server_v2.py"],
  "cwd": "C:\\phantom-mcp",
  "env": {}
}
```

Or import the ready-made config from [`jan_config.json`](../jan_config.json).

4. Save. Restart Jan.

### 5. Pick a model with tool use enabled

Load any model that supports function/tool calling.
Make sure the **Tool Use** toggle is on in the chat panel.
Set the **Context Length** to at least 16384 (32768 if you can spare
the VRAM).

### 6. Try a sanity command

In the chat, send:

> Show me a list of phantom tools you have available, then take a
> screenshot and tell me what you see.

The model should call `list_tools` (your AI host does this automatically),
then call `desktop_screenshot`, and describe your screen.

If the model says it has no tools, the most common causes are:
- The model doesn't support tool use, or the Tool Use toggle is off.
- The MCP server failed to start. Look in `logs\server_v2.log` for the reason.
- You're pointing at the wrong working directory or Python path.

---

## How the model "remembers" things

Phantom gives the model its own memory under `data\phantom_memory\`.
You can open this folder at any time to see what it has stored.

There are five kinds of memory:

| Kind     | What it stores                                  | Tool prefix          |
|----------|-------------------------------------------------|----------------------|
| Facts    | Stable facts about your PC/setup                | `memory_fact_*`      |
| Tasks    | Step-by-step record of work in progress         | `memory_task_*`      |
| Traces   | Auto-recorded log of every tool the model ran   | `memory_trace_*`     |
| Lessons  | Distilled "what to do when X happens" guidance  | `memory_lesson_*`    |
| Notes    | Large free-form text the model wrote (chunked)  | `memory_note_*`      |

The model can search facts (`memory_fact_search`), look back at recent
actions (`memory_trace_recent`), or have phantom distill repeated
failures into a lesson (`memory_learn_from_traces`). When the trace log
gets large, run `memory_compact` to summarize older history into a
single fact and keep things lean.

**Phantom owns its own memory.** It tracks environment/desktop state
and the model's task history. If you also run a different MCP server
that does its own memory, the two are independent — they do not auto-sync,
and that is intentional.

---

## When things go wrong

- **Look in `logs\server_v2.log`.** Phantom writes every tool call,
  every error, and every retry there.
- **Use `memory_trace_failures` from the chat.** Ask the model to call
  it and report what's been failing.
- **Stop everything fast:** move your mouse to the top-left corner
  (0,0). PyAutoGUI's emergency brake fires immediately and aborts any
  in-progress mouse/keyboard action.
- **Reset memory:** delete files inside `data\phantom_memory\`. The
  next run will recreate them empty.

---

## Where to go from here

- Read `SYSTEM_PROMPT.md` for the full system prompt to put in your AI host.
- Read `docs/mind-body.md` for the mental model (LM Studio = mind, Phantom = body).
- Read `docs/refactor-plan.md` for the v2 architecture story.
- Skim `phantom/tools/__init__.py` to see every tool the server registers.
