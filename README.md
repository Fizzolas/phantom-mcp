# 👻 Phantom MCP Server

**A full PC-control MCP server for LM Studio — your AI embedded in FizzBeast.**

---

## What This Is

Phantom is a [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server that gives any LLM running in LM Studio the ability to:

| Capability | How |
|---|---|
| 👁 See the screen | `screenshot` tool → returns base64 PNG |
| 🖱 Control mouse | `mouse_click`, `mouse_move`, `mouse_scroll` |
| ⌨ Type & hotkeys | `keyboard_type`, `keyboard_hotkey` |
| 💻 Run commands | `run_cmd`, `run_powershell`, `run_persistent_cmd` |
| 📁 Read/write files | `read_file`, `write_file` with ownership auth |
| 🔬 PC hardware info | `pc_snapshot` — live CPU/RAM/GPU readings |
| 🪟 Window control | `list_windows`, `focus_window`, `minimize_window` |
| 🧠 Memory | `memory_save`, `memory_get`, `memory_compress` |
| ⚙ Processes | `list_processes`, `kill_process`, `launch_app` |

---

## Your PC (FizzBeast — auto-detected at startup)

| Component | Spec |
|---|---|
| CPU | Intel Core i7-13620H (10 cores / 16 threads) |
| RAM | 32 GB DDR5 @ 5600 MHz |
| GPU | NVIDIA RTX 4070 Laptop (8 GB VRAM) |
| Storage | C: 926 GB NVMe (WD) · D: 477 GB NVMe (Kingston) |
| OS | Windows 10 (Build 26200) |
| Python | 3.11.9 at C:\Program Files\Python311 |
| CUDA | 12.4 |
| Model | Gemma 4 E4B Q4_K_M |

---

## Installation

### Step 1 — Copy files
Place this entire folder at `C:\phantom-mcp\`

```bat
git clone https://github.com/Fizzolas/phantom-mcp C:\phantom-mcp
```

### Step 2 — Install missing packages
```bat
"C:\Program Files\Python311\python.exe" -m pip install pystray pygetwindow
```
> Everything else (mss, pyautogui, mcp, httpx, GPUtil, psutil, Pillow) is already installed on FizzBeast.

### Step 3 — Connect LM Studio
1. Open LM Studio → Settings → MCP Servers
2. Click **Add Server** → **Manual / stdio**
3. Fill in:
   - **Command:** `C:\Program Files\Python311\python.exe`
   - **Args:** `C:\phantom-mcp\server.py`
4. Save and restart LM Studio.

### Step 4 — Load your model
Load **Gemma 4 E4B Q4_K_M** (or any model). Enable **Tool Use** in the LM Studio chat panel.

### Step 5 — Set this system prompt
```
You are Phantom, an autonomous AI agent embedded in this PC. When given a goal, break it into steps and execute them using your tools. After every action, take a screenshot to verify what happened. Keep working until the goal is fully complete. Never say 'I cannot do that' — use your tools. If a tool fails, try an alternate approach. You do not stop until done.
```

### Step 6 — Test
```
Take a screenshot of my screen and tell me what you see.
```

---

## File Ownership & Auth System

- **Agent-created files** — tracked in `data/agent_files.json`. AI can freely modify these.
- **Your files / system files** — a **popup dialog** appears asking you to approve or deny.
- No silent edits to your work. Ever.

---

## Memory System

```
memory_save    → store a fact for later
memory_get     → recall it by key
memory_search  → fuzzy search all memories
memory_compress → summarize a conversation and store it as a digest
```

Stored in `data/memory.json` — plain JSON you can open and edit.

---

## Emergency Brake

**Move your mouse to the top-left corner (0,0).** PyAutoGUI FAILSAFE triggers instantly and kills all mouse/keyboard actions.

---

## File Structure

```
C:\phantom-mcp\
├── server.py               ← Main MCP server (entry point)
├── launch.bat              ← Double-click to start
├── requirements.txt
├── lmstudio_config.json    ← Paste into LM Studio MCP settings
├── tools/
│   ├── pc_vision.py        ← Screenshot
│   ├── mouse_kb.py         ← Mouse + keyboard
│   ├── shell.py            ← CMD, PowerShell, persistent shell
│   ├── file_ops.py         ← File operations
│   ├── auth_guard.py       ← Auth popup for user files
│   ├── process_ops.py      ← Process list/kill/launch
│   ├── pc_info.py          ← Live hardware snapshot
│   └── window_ops.py       ← Window management
├── memory/
│   └── manager.py          ← Persistent memory + compression
├── ui/
│   └── tray.py             ← System tray icon
├── data/                   ← Auto-created at runtime
└── logs/                   ← server.log lives here
```
