# Changelog

All notable changes to Phantom-MCP are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased] — v2 Refactor Pass 1–7 + Improvement Roadmap

### Added

#### Skill Layer (Ouro Self-Improving Loop) — Part 4
- `phantom/tools/skills.py` — new `skill_*` tool namespace (8 tools):
  - `skill_promote` — crystallise a successful procedure into a named, versioned skill stored in phantom memory.
  - `skill_get` — load full skill record including steps, stats, version.
  - `skill_list` — list all active skills with use/success/fail counts.
  - `skill_search` — word-overlap retrieval of skills by query.
  - `skill_load_relevant` — retrieve skills matching the current goal at session start; returns full steps ready to adopt into the plan.
  - `skill_record_outcome` — record success/failure after using a skill; keeps running stats.
  - `skill_improve` — replace a skill's steps with an improved version; increments version number.
  - `skill_deprecate` — retire a skill that is no longer viable; record is kept for audit.
- Skills are stored as `skill:<name>` prefixed facts in `data/phantom_memory/facts.json`; no new files needed.
- `phantom/tools/__init__.py` — wired `phantom.tools.skills` into boot-time registry import chain.
- `SYSTEM_PROMPT.md` updates:
  - `skill_load_relevant` added to Ouro loop (between `agent_goal_start` and `agent_understand`).
  - `skill_record_outcome` added to loop tail (before `notify_user`).
  - New **Skill Growth (Ouro Loop)** section describing when to promote/improve/deprecate skills.
  - Core Rule 8: "After 2+ successful uses of same procedure: call `skill_promote`."
  - Session-boot routine updated: `memory_task_list` → `agent_status` → `skill_list` → resume.
  - Skills row added to tool table.
  - Skills entry added to Memory Namespaces section.
  - Jan.ai API base URL added to Machine section.
- `CHANGELOG.md` — this file.

#### Install & Launch Hardening — Part 1
- `server_v2.py`:
  - `_validate_environment()`: pre-flight check at boot (Python ≥3.8, `phantom/` exists, `logs/` and `data/` writable, `mcp` + `pydantic` importable). Human-readable error on failure; no raw traceback.
  - Working-directory guard: clear message if run from wrong folder.
  - Graceful shutdown: SIGINT/SIGTERM handlers flush log file and wait for in-flight memory writes.
- `requirements-core.txt` — minimal install file (`mcp`, `pydantic`, `httpx`, `python-dotenv`). Fast install path that guarantees the server can start.
- `install.bat` — full installer: Python version check with download URL, `.venv` creation, core-first install, full install with graceful optional-dep failures, creates `data\phantom_memory\`, saves `.python_cmd.txt`.
- `launch.bat` — robust launcher: reads `.python_cmd.txt`, falls back to `.venv\Scripts\python.exe`, checks `server_v2.py` exists before running, shows 5 diagnostic tips on crash.
- `.gitignore` — expanded: `.venv/`, `venv/`, `*.tmp`, `*.jsonl.tmp`, `*.corrupt.*`, `.python_cmd.txt`, OS/editor junk.

#### Memory Reliability — Part 2 (already in store.py)
- `phantom/memory/store.py`:
  - Trace auto-rotation: `trace_append` triggers background `compact()` when `traces.jsonl` exceeds `TRACE_AUTO_ROTATE_LINES` (1,000) lines. Fire-and-forget via `asyncio.create_task`; never blocks the calling tool.
  - Startup integrity check (`_verify_integrity()`): scans `facts.json`, `tasks.json`, `lessons.json`, and `traces.jsonl` at boot; logs clear warnings for corrupt or malformed entries.
  - `learn_from_traces`: unified `host_model_id` param; `lms_base_model_id` kept as legacy alias.
  - All async store methods (`fact_set`, `task_start`, `task_step`, `task_finish`, `lesson_set`, `note_save`, `compact`) wrapped correctly as `async def` in tool layer (fixes "coroutine never awaited" bug from sync wrappers).
  - `trace_append`: all disk errors caught and logged; never raises into calling tool path.

### Changed

#### Documentation — Part 3
- `README.md`:
  - Quick Start rewritten: removed `py -m pip install -r requirements.txt`; replaced with `install.bat` double-click.
  - LM Studio args corrected: `Command` is now `C:\phantom-mcp\.venv\Scripts\python.exe` (full venv path); `Args` is `C:\phantom-mcp\server_v2.py`.
  - Jan.ai setup section added with full JSON config snippet.
  - File layout table updated: added `jan_config.json`, `requirements-core.txt`, `install.bat`, `launch.bat`, `SYSTEM_PROMPT.md`.
  - Removed false "82 tests passing" claim; replaced with `pytest test suite`.
- `docs/getting-started.md`:
  - Step 3 (install) rewritten from manual `pip` commands to `install.bat`; explains what it does, notes you only run it once.
  - Removed ghost reference to `requirements-optional.txt` (file does not exist).
  - LM Studio args corrected to use full venv path with explanation of why.
  - Jan.ai setup section added.
  - References to "LM Studio" updated to "LM Studio or Jan.ai" throughout.
- `SYSTEM_PROMPT.md`:
  - First line updated: added `skill_*` to the mandatory-tool list alongside `agent_*` and `memory_*`.

### Fixed

- `requirements.txt` reorganised: CORE (4 packages the server cannot run without) labelled at top; optional packages grouped with comments explaining what breaks if missing. No packages removed.
- `phantom/tools/memory.py`: all async tool functions changed from `def` to `async def` to match the store methods they call — fixes silent "coroutine was never awaited" bug in MCP server's event loop.
- `phantom/memory/store.py`: `_read_json` now renames corrupt files to `<name>.corrupt.<ts>` before resetting to default — preserves evidence without crashing.
- `phantom/memory/store.py`: `compact()` writes trimmed trace file atomically (tmp → rename) before recording summary fact — crash-safe ordering.

---

## Prior History

Phantom-MCP was originally a monolithic script (`server.py`). The v2 refactor
split it into the `phantom/` package (Pass 1–7), introducing:
- `phantom/tools/` — per-category tool modules with Pydantic schemas.
- `phantom/memory/` — durable, file-backed memory store.
- `phantom/cognition/` — agent self-check and planning layer.
- `phantom/runtime/` — async executor with timeout and capability gating.
- `phantom/contracts.py` — uniform `ToolResult` envelope.

See `docs/refactor-plan.md` for the full v2 architecture story.
