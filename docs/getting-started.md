# Phantom MCP — Getting Started

This guide is written for someone who is comfortable using their PC and
running apps, but does not assume Python or MCP background knowledge.
If you get stuck on any step, copy the error and ask your AI assistant
to help.

---

## What this server actually does

Phantom-MCP is a small program that runs on your PC and exposes a set
of "tools" to the language model loaded in LM Studio. The model can
call these tools to take screenshots, click, type, run shell commands,
open files, focus windows, remember things between sessions, and so on.

**You still talk to the model through the LM Studio chat box.** Phantom
is not a chat UI — it is the model's hands.

---

## Two big rules to know up front

1. **Context windows are finite.** Every tool result has to fit inside
   the model's context window. Phantom enforces this automatically: big
   outputs get trimmed with a sentinel message so the model knows there
   was more. Bigger context (16k, 32k+) gives the model more room to
   remember things; smaller context (4k–8k) still works but the model
   forgets older steps faster.

2. **mcp.json launches MCP servers — nothing else.** Your model name,
   context length, GPU offload, etc. live inside LM Studio's own
   settings. Don't try to put them in `mcp.json`. The example config in
   this repo only contains the launch command.

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

If `py` works but `pip` doesn't, run `py -m ensurepip --upgrade` once.

### 3. Install the Python packages Phantom needs

```bat
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
```

If something in `requirements-optional.txt` matters to you (e.g.
playwright for browser automation), install it too:

```bat
py -m pip install -r requirements-optional.txt
```

### 4. Tell LM Studio about the server

1. Open LM Studio.
2. Go to **Settings → MCP Servers → Add Server (Manual / stdio)**.
3. Paste the values from `lmstudio_config.json`:
   - **Command:** `python` (or the full path to your Python, e.g. `C:\Program Files\Python311\python.exe`)
   - **Args:** `server_v2.py`
   - **Working directory:** `C:\phantom-mcp`
4. Save. Restart LM Studio.

Phantom is self-contained — you don't need any other MCP server
running. If you happen to use one (any second server is fine), they
each get their own stdio pipe and won't interfere.

### 5. Pick a model with tool use enabled

In LM Studio's model panel, load any model that supports function/tool
calling. Make sure the **Tool Use** toggle is on in the chat panel.
Set the **Context Length** to at least 16384 (32768 if you can spare
the VRAM).

### 6. Try a sanity command

In the LM Studio chat, send:

> Show me a list of phantom tools you have available, then take a
> screenshot and tell me what you see.

The model should call `list_tools` (LM Studio does this automatically),
then call `desktop_screenshot`, and describe your screen.

If the model says it has no tools, the most common causes are:
- LM Studio is set to a model that doesn't support tool use.
- The MCP server failed to start. Look in `logs/server_v2.log` for the
  reason.
- You're pointing at the wrong working directory.

---

## How the model "remembers" things

Phantom gives the model its own memory under `data/phantom_memory/`.
You can open this folder at any time to see what it has stored.

There are four kinds of memory:

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
that does its own memory, the two are independent — they do not auto-
sync, and that is intentional.

---

## When things go wrong

- **Look in `logs/server_v2.log`.** Phantom writes every tool call,
  every error, and every retry there.
- **Use `memory_trace_failures` from the chat.** Ask the model to call
  it and report what's been failing.
- **Stop everything fast:** move your mouse to the top-left corner
  (0,0). PyAutoGUI's emergency brake fires immediately and aborts any
  in-progress mouse/keyboard action.
- **Reset memory:** delete files inside `data/phantom_memory/`. The
  next run will recreate them empty.

---

## Where to go from here

- Read `SYSTEM_PROMPT.md` for the full system prompt to put in LM Studio.
- Read `docs/refactor-plan.md` for the architecture story.
- Skim `phantom/tools/__init__.py` to see every tool the server registers.
