# Phantom-MCP Tool Catalog

This file is generated from the live tool registry.
Do not edit by hand — run `python scripts/gen_docs.py` instead.

**Total tools:** 50 across 9 categories.

## clipboard

| Tool | Needs | Timeout | Summary |
| ---- | ----- | ------- | ------- |
| `clipboard_get` | — | 5s | Read the current contents of the system clipboard as text. |
| `clipboard_set` | — | 5s | Place `text` on the system clipboard. Returns the number of chars written. |

## files

| Tool | Needs | Timeout | Summary |
| ---- | ----- | ------- | ------- |
| `dir_list` | — | 15s | List immediate children of a directory. Use dir_tree for recursive listings. |
| `dir_tree` | — | 30s | Render a shallow directory tree under `root`, limited to `max_files` |
| `file_append` | — | 30s | Append `content` to the end of a text file, creating it if needed. |
| `file_delete` | — | 10s | Delete a file. Irreversible — the tool layer may gate this in future PRs. |
| `file_exists` | — | 5s | Check whether a file or directory exists at `path`. |
| `file_read` | — | 30s | Read a text file and return its contents. Use for < 1 MB files. |
| `file_search` | — | 30s | Find files matching a glob `pattern` under `root`. |
| `file_write` | — | 30s | Overwrite a file with `content`. Creates parent directories as needed. |

## input

| Tool | Needs | Timeout | Summary |
| ---- | ----- | ------- | ------- |
| `keyboard_key` | desktop | 10s | Press a single key or hotkey combination. Pass 'enter', 'esc', |
| `keyboard_type` | desktop | 30s | Type `text` at the current focus, character by character. |
| `mouse_click` | desktop | 10s | Click at (x, y). Use `clicks=2` for double-click, `button='right'` for |
| `mouse_drag` | desktop | 15s | Drag the cursor from one point to another while holding `button`. |
| `mouse_move` | desktop | 10s | Smoothly move the cursor to screen coordinates (x, y). |
| `mouse_scroll` | desktop | 10s | Scroll at (x, y). Positive `clicks` scrolls up, negative down. |

## memory

| Tool | Needs | Timeout | Summary |
| ---- | ----- | ------- | ------- |
| `memory_delete` | — | 5s | Forget a key. Idempotent — deleting a non-existent key is not an error. |
| `memory_get` | — | 5s | Retrieve a value previously saved with memory_save. If the key doesn't |
| `memory_list` | — | 5s | List all keys currently in memory. |
| `memory_save` | — | 5s | Store `value` under `key`. Overwrites any existing value for that key. |
| `memory_search` | — | 10s | BM25-ranked search over saved facts and task summaries. |
| `task_list` | — | 5s | List known tasks, most recently updated first. Pass status='in_progress' |
| `task_load` | — | 5s | Read back a task record — goal, status, step history, summary. Use to |
| `task_start` | — | 5s | Begin a durable task record. Use at the start of any multi-step job |
| `task_update` | — | 5s | Append a step to a task's history and optionally update its status. |

## notify

| Tool | Needs | Timeout | Summary |
| ---- | ----- | ------- | ------- |
| `notify_user` | desktop | 10s | Show a desktop toast/notification to the user. |

## system

| Tool | Needs | Timeout | Summary |
| ---- | ----- | ------- | ------- |
| `find_process` | — | 10s | Find running processes whose command line contains `name`. |
| `kill_process` | — | 15s | Terminate a process. `target` is either a PID (int) or a name |
| `launch_app` | desktop | 310s | Start a desktop application by path or name. Returns launch metadata. |
| `list_processes` | — | 10s | List running processes sorted by RAM / CPU / PID / name. |
| `shell_exec` | — | 310s | Run a command in the host's shell, PowerShell, or an inline Python. |
| `system_info` | — | 5s | Return a snapshot of the host PC: CPU load, RAM/swap, disks, GPU, network. |

## ui

| Tool | Needs | Timeout | Summary |
| ---- | ----- | ------- | ------- |
| `active_window` | desktop | 5s | Return the currently focused window's title and pid. |
| `focus_window` | desktop | 10s | Bring a window to the foreground. Case-insensitive substring match by |
| `list_windows` | desktop | 5s | List all visible top-level windows with title, pid, and position. |
| `window_move` | desktop | 10s | Move a window so its top-left corner is at (x, y). |
| `window_rect` | desktop | 5s | Return a window's bounding rect: {x, y, width, height}. |
| `window_resize` | desktop | 10s | Resize a window to `width` x `height`. |
| `window_state` | desktop | 10s | Change a window's state: minimize, maximize, or restore. Collapses the |

## vision

| Tool | Needs | Timeout | Summary |
| ---- | ----- | ------- | ------- |
| `ocr_screen` | tesseract, display | 20s | Capture a screen region and run Tesseract OCR on it. |
| `screen_info` | display | 5s | Return info about attached displays: count, primary size, DPI. |
| `screenshot` | display | 15s | Capture the screen and return it as a base64 PNG. |

## web

| Tool | Needs | Timeout | Summary |
| ---- | ----- | ------- | ------- |
| `finance` | playwright | 45s | Google Finance quote lookup. `query` can be a ticker ('NVDA'), a |
| `maps` | playwright | 45s | Google Maps places search — returns name, address, rating, URL. |
| `search` | playwright | 60s | Unified search tool. Pick `kind`: |
| `translate` | playwright | 30s | Translate `text` from `source` to `target` (ISO-639 language codes). |
| `trends` | playwright | 45s | Google Trends interest-over-time for `query`. |
| `visit_page` | playwright | 60s | Fetch a URL and return the extracted main-content text. |
| `weather` | playwright | 45s | Current weather + short-term forecast for a location. |
| `web_search` | playwright | 45s | Google web search. Returns a list of {title, url, snippet}. |

