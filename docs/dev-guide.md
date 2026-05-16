# Phantom MCP — Development Guide

This guide is for anyone (human or AI assistant) adding new tools,
fixing bugs, or extending Phantom-MCP. It explains the architecture
conventions, the patterns every tool must follow, and the exact prompts
to use when asking an AI to help write new code.

---

## Architecture in one paragraph

Phantom-MCP is a Python MCP server (`server_v2.py`) that wraps a
`phantom/` package. Tools live in `phantom/tools/` — one file per
category. Every tool is a plain Python function decorated with `@tool()`
from `phantom/tools/_base.py`. The decorator registers the function with
a global `ToolRegistry` at import time. At boot, `server_v2.py` imports
`phantom/tools/__init__.py`, which calls `_safe_import_tool_module()` for
every category module. Missing optional dependencies cause that module to
be skipped — the server still starts. Memory lives in
`phantom/memory/store.py` (file-backed, async-safe). Cognition lives in
`phantom/cognition/core.py` (pure Python, reads/writes memory only).

---

## File map

```
phantom-mcp/
├── server_v2.py              # Entry point. Boot, MCP loop, shutdown.
├── SYSTEM_PROMPT.md          # Paste into LM Studio / Jan.ai system prompt.
├── requirements.txt          # All deps (CORE section + OPTIONAL section).
├── requirements-core.txt     # Minimal install (4 packages).
├── install.bat               # Windows installer. Run once.
├── launch.bat                # Windows launcher. Run every session.
├── lmstudio_config.json      # Copy into LM Studio MCP config.
├── jan_config.json           # Copy into Jan.ai MCP config.
├── CHANGELOG.md              # What changed and when.
├── phantom/
│   ├── contracts.py            # ToolResult envelope: ok(), fail().
│   ├── tools/
│   │   ├── _base.py            # @tool decorator + ToolRegistry.
│   │   ├── __init__.py         # Imports every tool module at boot.
│   │   ├── memory.py           # memory_* tools.
│   │   ├── skills.py           # skill_* tools (Ouro layer).
│   │   ├── cognition.py        # agent_* tools.
│   │   ├── desktop.py          # desktop_* tools.
│   │   ├── files.py            # file_* tools.
│   │   ├── shell.py            # shell_* tools.
│   │   ├── windows.py          # window_* tools.
│   │   ├── processes.py        # process_* tools.
│   │   ├── clipboard.py        # clipboard_* tools.
│   │   ├── notify.py           # notify_user tool.
│   │   ├── pc_info.py          # system_info tool.
│   │   ├── web_search.py       # web_search tool.
│   │   └── ocr.py              # Legacy OCR wrapper (desktop.py is preferred).
│   ├── memory/
│   │   └── store.py            # PhantomMemory class.
│   ├── cognition/
│   │   └── core.py             # AgentCognition class.
│   ── runtime/
│       └── executor.py         # safe_call(): timeout + async dispatch.
├── tools/                    # Legacy tool helpers (imported by phantom/tools/*).
│   ├── mouse_kb.py             # pyautogui wrappers.
│   └── pc_vision.py            # mss + tesseract wrappers.
├── data/
│   └── phantom_memory/         # All runtime memory files (auto-created).
├── logs/
│   └── server_v2.log           # Runtime log.
├── tests/                    # pytest test suite.
└── docs/
    ├── getting-started.md
    ├── mind-body.md
    ├── refactor-plan.md
    └── dev-guide.md            # This file.
```

---

## How to add a new tool — the pattern

Every tool follows the exact same four-step pattern. Do not deviate.

### Step 1 — Pick the right file

Add to an existing `phantom/tools/*.py` if the tool fits the category
(e.g. a new `file_move` goes in `files.py`). If it is a genuinely new
category, create `phantom/tools/<category>.py` and add one line to
`phantom/tools/__init__.py`:

```python
_safe_import_tool_module("phantom.tools.<category>")
```

### Step 2 — Write a Pydantic input schema

```python
from pydantic import BaseModel, ConfigDict, Field

class MyToolInput(BaseModel):
    path: str = Field(..., description="Absolute path to the file.")
    encoding: str = Field("utf-8", description="File encoding.")
    model_config = ConfigDict(extra="forbid")   # always include this
```

Rules:
- Every field needs a `description` — it appears in the model's tool list.
- Use `...` (ellipsis) for required fields, a default value for optional ones.
- Always set `extra="forbid"` so bad argument names surface as clear errors.
- Keep string max-lengths reasonable (`max_length=4000` for free text, smaller for keys/names).

### Step 3 — Write the function

```python
from phantom.contracts import ok, fail
from phantom.tools._base import tool

@tool(
    "my_tool_name",          # snake_case; namespaced by category (e.g. file_move)
    category="files",        # matches the module name
    schema=MyToolInput,
    needs=(),                # tuple of capability strings if deps are optional
    timeout_s=10.0,
)
async def my_tool_name(path: str, encoding: str = "utf-8") -> dict:
    """
    One-paragraph docstring. First sentence: what the tool does.
    Second sentence: when to use it. Third (optional): caveats.
    This docstring is what the model sees in list_tools.
    """
    try:
        # ... do the work ...
        return ok({"result": "..."})
    except FileNotFoundError:
        return fail(f"File not found: {path}", category="client_error")
    except Exception as e:
        return fail(str(e), category="server_error")
```

Rules:
- Use `async def` if the work is I/O-bound or calls an async store method.
  Use `def` only for pure in-memory computation.
- Always return `ok(...)` or `fail(...)` — never raise, never return a raw dict.
- `ok(payload, hint="...", chars=N)` — `hint` is optional guidance for the model.
- `fail(message, category="...")` — category is one of: `client_error`,
  `server_error`, `timeout`, `not_available`.
- If the tool depends on an optional package (e.g. `pyautogui`), add
  `needs=("desktop",)` and import the package inside the function body,
  not at the top of the file.

### Step 4 — Add to SYSTEM_PROMPT.md

Add the new tool name to the correct row in the tool table inside
`SYSTEM_PROMPT.md`. If you created a new category, add a new row.
Then add usage guidance to the appropriate section of the prompt.

---

## Prompts to use with an AI assistant

Copy these verbatim when asking an AI (like this one) to write code for Phantom.
The context block at the top of each prompt tells the AI everything it needs
to write code that actually fits the codebase without you explaining the whole
architecture every time.

---

### Prompt A — Add a new tool to an existing category

```
You are working on the Phantom-MCP codebase.
Architecture summary:
- Tools live in phantom/tools/<category>.py.
- Each tool is a Python function decorated with @tool() from phantom/tools/_base.py.
- Input schema is a Pydantic BaseModel with extra="forbid".
- Return ok({...}) or fail(message, category="...") from phantom.contracts.
- Use async def for I/O; def for pure computation.
- Optional-dep imports go inside the function body, not at the top.
- The @tool decorator takes: name (str), category (str), schema (class),
  needs (tuple, optional), timeout_s (float, optional).

Task: Add a new tool called `<tool_name>` to `phantom/tools/<file>.py`.
It should: <describe what it does in plain English>.
Inputs: <list the input fields, their types, and what they mean>.
Return: <describe the return dict fields>.

Write only the Pydantic schema class and the decorated function.
Do not rewrite the whole file. Follow the exact pattern already in the file.
```

---

### Prompt B — Create a new tool category

```
You are working on the Phantom-MCP codebase.
Architecture summary:
- New tool categories go in phantom/tools/<category>.py.
- After creating the file, add one line to phantom/tools/__init__.py:
    _safe_import_tool_module("phantom.tools.<category>")
  Insert it in dependency order: pure-Python tools before desktop-only tools.
- Each tool follows the same @tool + Pydantic + ok/fail pattern.
- Optional deps are imported inside function bodies and gated with
  needs=("<capability>",) in @tool.

Task: Create a new tool category called `<category>` in
`phantom/tools/<category>.py`.
Tools to include:
  - <tool_1>: <description>
  - <tool_2>: <description>

Also write the single line to add to phantom/tools/__init__.py.
Also add a new row to the tool table in SYSTEM_PROMPT.md for this category.
```

---

### Prompt C — Fix a bug in an existing tool

```
You are working on the Phantom-MCP codebase.
File: phantom/tools/<file>.py
Tool: <tool_name>

Bug: <describe the symptom — what the tool returns or does wrong>.
Expected: <what it should return or do>.

Rules to follow:
- Do not change the function signature or the @tool registration.
- Do not change how ok() or fail() are called (do not return raw dicts).
- Do not add new imports at the top of the file if they are for optional deps
  (put them inside the function body).

Write only the corrected function body. Show a unified diff or the full
corrected function, whichever is shorter.
```

---

### Prompt D — Add a method to PhantomMemory (store.py)

```
You are working on phantom/memory/store.py in the Phantom-MCP codebase.
PhantomMemory is a file-backed, async-safe class.
Conventions:
- Reads: sync def, no lock needed (Python GIL + single-process).
- Writes: async def, wrapped in `async with self._alock:`.
- File writes use _write_json() (atomic: write to .tmp then os.replace).
- Trace appends use the append mode in trace_append (covered by _alock).
- All disk errors in trace_append are caught and logged; never raised.
- Public API methods return plain Python dicts.

Task: Add a new method called `<method_name>` that:
<describe what it reads/writes and what it returns>.

Write only the new method. Do not rewrite the class.
```

---

### Prompt E — Update the SYSTEM_PROMPT after adding tools

```
You are updating SYSTEM_PROMPT.md for Phantom-MCP.
The file already contains:
- A Ouro loop section.
- A Core Rules section.
- A tool table with rows for each category.
- A Memory Namespaces section.

Task: I have just added the following tools:
  - <tool_1> (category: <category>) — <one-sentence description>
  - <tool_2> (category: <category>) — <one-sentence description>

Make these changes to SYSTEM_PROMPT.md:
1. Add the new tools to the correct row(s) in the tool table.
2. If I created a new category, add a new row to the table.
3. If any tool should appear in the Ouro loop, show me where to insert it
   and what the new loop text should look like.
4. If any tool has important usage rules (e.g. "call X before Y"),
   add a short rule to the appropriate section.

Write only the changed sections, not the whole file.
```

---

### Prompt F — Write or update a test

```
You are writing a pytest test for the Phantom-MCP codebase.
Test files live in tests/. Existing tests use:
  - A tmp_path fixture (pytest built-in) to create a temp data directory.
  - PhantomMemory(tmp_path) to create an isolated store.
  - pytest.mark.asyncio for async tests.
  - Direct calls to store methods or tool functions (not the MCP server).

Task: Write a test for <tool or method name> that covers:
  - <happy path scenario>
  - <error/edge case scenario>

Use the tmp_path fixture. Do not mock PhantomMemory — use a real instance
pointing at a temp directory.
```

---

## What NOT to do

- **Do not** `import *` from any phantom module.
- **Do not** call `asyncio.get_event_loop().run_until_complete()` inside
  a tool function. If you need to call an async store method, make the
  tool `async def`.
- **Do not** return raw strings or raw dicts from a tool. Always use
  `ok()` or `fail()`.
- **Do not** add top-level imports for packages that might be missing
  (pyautogui, mss, pytesseract, playwright, etc.). Put them inside
  the function body and add `needs=(...)` to the decorator.
- **Do not** write to `data/phantom_memory/` directly. Always go through
  `PhantomMemory` methods.
- **Do not** add a tool to the SYSTEM_PROMPT tool table without writing
  the actual function. The model will try to call it and get
  `Unknown tool` errors.

---

## Capability strings for `needs=(...)`

| String | What it gates | Missing dep |
|---|---|---|
| `desktop` | pyautogui, mss (screen capture + mouse/keyboard) | `pyautogui`, `mss` |
| `display` | requires a display/screen (headless machines) | X display or Windows desktop |
| `tesseract` | OCR tools | Tesseract-OCR binary |
| `playwright` | browser automation | `playwright` Python package + browsers |

Add new capability strings in `server_v2.py` inside the boot capability
probe function, and check for them with `needs=("<string>",)` in `@tool`.

---

## Running tests

```bat
.venv\Scripts\python.exe -m pytest tests/ -v
```

Or from an already-activated venv:

```bat
pytest tests/ -v
```

Tests do not require a running server, a display, or LM Studio.
All tool functions and store methods can be tested in isolation.
