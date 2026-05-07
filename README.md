# 👻 Phantom MCP Server

**Desktop-control MCP server for LM Studio. Your model gets eyes,
hands, a shell, durable memory, and self-learning.**

> Need step-by-step setup? Read [`docs/getting-started.md`](docs/getting-started.md).
> It's written for someone who knows their way around their PC but is new to Python and MCP.
>
> Want the mental model? Read [`docs/mind-body.md`](docs/mind-body.md):
> LM Studio is the mind, Phantom is the body, and the `agent_*` tools
> are the reflective scaffolding between them.

---

## What this is

Phantom is a [Model Context Protocol](https://modelcontextprotocol.io)
server that gives the LLM you've loaded in LM Studio the tools it needs
to actually do things on your machine. The model still talks to you in
LM Studio's chat box — phantom is the model's hands, not a separate UI.

### Mind / Body model

Phantom is built around a simple split:

- **LM Studio is the mind.** It holds the conversation, plans, reasons,
  writes language, and chooses which tool to call next.
- **Phantom is the body.** It perceives (screenshots, OCR, file reads),
  acts (clicks, keystrokes, shell commands, file writes), remembers
  (facts/tasks/traces/lessons/notes), and offers the mind reflective
  scaffolding (`agent_*` tools) for self-checking before it acts.

Phantom does not run on its own. Every tool call originates from the
mind in LM Studio, including the cognition tools — there is no hidden
background loop. The cognition layer is there so a small local model
does not have to reinvent goal tracking, planning, risk-checking, and
self-reflection from scratch on every turn.

| Capability         | Tools                                                                 |
|--------------------|-----------------------------------------------------------------------|
| 👁 See the screen | `desktop_screenshot`, `desktop_screen_info`, `ocr_screen`             |
| 🖱 Mouse           | `desktop_click`, `desktop_move`, `desktop_scroll`, `desktop_drag`     |
| ⌨ Keyboard        | `desktop_type`, `desktop_hotkey`, `desktop_key_press`                 |
| 🪟 Windows         | `window_list`, `window_focus`, `window_active`, `window_resize`, etc. |
| ⚙ Processes       | `process_list`, `process_find`, `process_kill`, `process_launch`      |
| 💻 Shell           | `shell_cmd`, `shell_powershell`, `shell_python`                       |
| 📁 Files           | `file_read`, `file_write`, `file_list_dir`, `file_search`, `file_read_tree` |
| 🧠 Memory          | `memory_fact_*`, `memory_task_*`, `memory_trace_*`, `memory_lesson_*`, `memory_note_*` |
| 🪞 Cognition       | `agent_goal_start`, `agent_understand`, `agent_plan_set`, `agent_plan_advance`, `agent_next_action`, `agent_status`, `agent_reflect`, `agent_action_review`, `agent_confidence_check`, `agent_risk_check`, `agent_checkpoint`, `agent_after_action_review`, `agent_stuck_detect`, `agent_replan`, `agent_recover` |
| 🔬 PC info         | `system_info`                                                         |
| 📋 Clipboard       | `clipboard_get`, `clipboard_set`                                      |
| 🔔 Notifications   | `notify_user`                                                         |
| 🌐 Web             | `web_search` (needs Playwright)                                       |

Tool names are deliberately namespaced (`desktop_*`, `memory_*`, etc.)
so the model never confuses them with another MCP server's tools.

---

## What's new in v2 (this overhaul)

| Before                                                  | Now                                                                                                                |
|---------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------|
| 1500-line `_dispatch` if-tree referencing ghost modules | A registry: each tool is a `@tool`-decorated function with a pydantic schema. Validation and routing in one place. |
| Tools that crashed if a dep was missing                 | Capability probe at boot; tools whose deps aren't installed are hidden from `list_tools` rather than blowing up.   |
| Outputs that could blow LM Studio's context             | A token-budget manager truncates every result to fit the loaded model's context, with a sentinel the model can see.|
| Memory was a single `memory.json`                       | `PhantomMemory` with facts / tasks / traces / lessons / chunked notes, all on disk under `data/phantom_memory/`.   |
| No record of what the model had been doing              | Every tool call is auto-traced with outcome and latency.                                                           |
| No way to learn from past failures                      | `memory_learn_from_traces` distills repeated failures into "lessons" the model can read.                            |
| Names like `mouse_click`, `keyboard_type`               | Renamed to `desktop_click`, `desktop_type` so LM Studio routes them clearly without colliding with other servers.   |
| `mcp.json` mixed model + server settings                | `lmstudio_config.json` now only contains the launch command. Model/context settings live inside LM Studio.         |

The legacy `server.py` is still in the repo — `server_v2.py` is the new
entry point and is what the docs and `lmstudio_config.json` point at.

---

## Quick start

```bat
git clone https://github.com/Fizzolas/phantom-mcp C:\phantom-mcp
cd C:\phantom-mcp
py -m pip install -r requirements.txt
```

In LM Studio → Settings → MCP Servers → **Add Server (Manual / stdio)**:

- **Command:** `python`
- **Args:** `server_v2.py`
- **Working directory:** the folder you cloned into

Restart LM Studio, load a tool-capable model, set context to 16k+, and
ask in chat:

> List the phantom tools you have, take a screenshot, and tell me what's on screen.

If anything goes wrong, look in `logs/server_v2.log`.

---

## Memory at a glance

Stored under `data/phantom_memory/`:

- `facts.json` — durable key/value facts the model learned about your environment.
- `tasks.json` — current and recent tasks (goal, steps, status).
- `traces.jsonl` — auto-recorded log of every tool call.
- `lessons.json` — distilled guidance ("when X fails, try Y").
- `notes/<label>/` — large free-form text the model wrote, chunked for re-loading.

You can open any of these files in a text editor. Delete the folder to
reset memory entirely.

**Phantom's memory is self-contained.** It owns desktop and environment
state. If you happen to run another MCP server alongside phantom (it is
not required — phantom stands on its own), phantom does not auto-sync
with it; the two memory stores remain independent on purpose.

---

## How outputs respect your context window

At boot, phantom asks LM Studio for the loaded model's context length
(via the SDK if available, otherwise the OpenAI-compatible REST API).
A `TokenBudget` is sized from that. Every tool result is passed through
`fit_any` before it ships back, so a 50,000-character file or a giant
shell output gets truncated with a sentinel like:

> ...<output truncated by phantom budget: showing 14,400 of 51,920 chars; re-run with a narrower query, pagination args, or raw=True>

The model can see that and choose to call again with a tighter range.
This is the "context economy" rule from the refactor plan — tool names,
descriptions, and outputs all compete for the same window.

---

## Emergency brake

Move your mouse to the top-left corner (0,0). PyAutoGUI's FAILSAFE
fires immediately and aborts any in-progress mouse/keyboard action.

---

## File layout

```
phantom-mcp/
├── server_v2.py              ← NEW: slim MCP entry point on the registry
├── server.py                 ← legacy entry point (kept; not used by v2)
├── lmstudio_config.json      ← example mcp.json fragment
├── requirements.txt
├── docs/
│   ├── getting-started.md    ← beginner-friendly setup
│   └── refactor-plan.md      ← architecture story
├── phantom/                  ← new package — registry, contracts, memory, cognition, tools
│   ├── contracts/            ← ToolResult envelope + error classifier
│   ├── runtime/              ← safe_call, capability probe, LM Studio probe, budget
│   ├── memory/               ← PhantomMemory store (facts/tasks/traces/lessons/notes)
│   ├── cognition/            ← AgentCognition: goals, plans, reflection, risk, AAR
│   └── tools/                ← every @tool lives here; one module per category
├── memory/                   ← legacy memory manager (used by server.py only)
├── tools/                    ← legacy tool implementations; phantom/tools wrap these
├── tests/                    ← pytest tests (82 tests, all passing as of v2)
├── data/                     ← created at runtime; memory state lives here
└── logs/                     ← server_v2.log lives here
```
