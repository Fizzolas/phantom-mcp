# Phantom-MCP Refactor Plan

This document is the single source of truth for the phantom-mcp
architectural refactor. Each PR implements one numbered section.

## North-star

Phantom-MCP is one interface for one agent: the model LM Studio has
loaded. Every design decision optimizes for:

1. **Survivability** — a tool never raises out of the server; it returns
   a structured `ToolResult`. The server never halts on failure; it
   reports and lets the model choose a fallback.
2. **Context economy** — tool names, descriptions, and outputs all
   compete for the same context window. Curate ruthlessly; summarize
   long outputs; paginate lists.
3. **Adaptivity** — discover the environment at boot (imports, binaries,
   loaded LM Studio model, context length) and advertise only what
   works. Re-probe when the environment changes.

## PR map

| PR  | Scope                                                                            | Status |
| --- | -------------------------------------------------------------------------------- | ------ |
| 1   | Contracts + registry + capability probe. Migrate 5 proof tools.                  | shipped ✓ |
| 2   | Memory adapter + task state machine. Un-expose chunk_* tools.                    | shipped ✓ |
| 3   | Migrate remaining tools. Remove ghost imports. Regenerate README + SYSTEM_PROMPT. | **this PR** |
| 4   | Planner loop, fallback chains, token-budget integration, LM Studio probe wiring. | pending |
| 5   | Tests (unit, contract, chaos), pyproject, launcher cleanup, observability.       | pending |

Each PR is independently shippable and leaves the server in a
more-working state than before.

## What PR 1 ships

- `phantom/contracts/` — `ToolResult` envelope, structured `MCPError`,
  exception classifier.
- `phantom/runtime/` — `safe_call` executor (fixes the async-in-to_thread
  bug), LM Studio probe (`probe_lmstudio`), token budget manager,
  capability probe.
- `phantom/tools/_base.py` — `@tool` decorator + `ToolRegistry` singleton.
- Five proof tools wrapped in the new layer:
  `system_info`, `clipboard_get`, `clipboard_set`, `notify_user`,
  `ocr_screen`, `web_search` (six, actually — clipboard counts as two).
  Each tool delegates to the existing legacy module; no legacy code is
  deleted or modified in this PR.
- Unit tests for contracts, executor, registry, and budget.

## What PR 1 does NOT change

- `server.py` still runs the old dispatch table. The new `phantom/`
  package is dormant until PR 4 wires it in.
- No legacy modules are edited or removed.
- No changes to `requirements.txt`, `install.bat`, or
  `lmstudio_config.json`.

That keeps this PR purely additive: it ships scaffolding without
changing runtime behavior.

## What PR 3 ships (this PR)

- `phantom/tools/shell.py` — `shell_exec(language, command, timeout_s)`
  collapses run_cmd/run_powershell/run_python behind a single `language`
  enum. One tool instead of three.
- `phantom/tools/process_ops.py` — `list_processes`, `find_process`,
  `kill_process` (now accepts PID *or* name), `launch_app`.
- `phantom/tools/file_ops.py` — eight file/dir tools with empty-path
  rejection at the schema layer.
- `phantom/tools/mouse_kb.py` — 6 tools (mouse_move/click/scroll/drag,
  keyboard_type/key) down from the legacy 11. `keyboard_key` auto-routes
  between single-press and hotkey depending on whether the key contains
  `+`.
- `phantom/tools/window_ops.py` — 7 tools; `window_state(state=
  minimize|maximize|restore)` collapses three legacy tools into one.
- `phantom/tools/vision.py` — `screenshot(region, hires)` collapses
  two legacy variants; `screen_info`.
- `phantom/tools/web.py` — `search(kind=web|news|scholar|images|
  shopping|books)`, `visit_page`, and typed helpers for trends/maps/
  finance/weather/translate. The per-kind search functions are collapsed
  into one tool with an enum.
- `scripts/gen_docs.py` — regenerates `docs/generated/tool-catalog.md`
  (Markdown table grouped by category) and
  `docs/generated/tool-system-prompt.md` (model-facing catalog) from
  `registry.all()`. The registry is now the sole source of truth; docs
  and code can no longer drift. The hand-written root `README.md` and
  `SYSTEM_PROMPT.md` still describe the legacy dispatcher and will be
  retired in PR 4.
- `tests/test_new_tools.py` — 48 new tests: registration coverage for
  every PR 3 tool, schema validation on each, a ghost-tool guard, and
  a sanity check that every registered tool has a docstring and JSON
  schema.
- `server.py` — non-functional cleanup: 40 ghost dispatch branches
  referencing modules that never existed (`browser_ops`, `db_ops`,
  `document_ops`, `input_ops`, `media_ops`, `system_ops`, `vision_ops`)
  were removed. No behavior change — every deleted branch would have
  raised `ModuleNotFoundError` at call time.

**Deliberate non-goals for PR 3** (per the Phil Schmid "curate ruthlessly"
rule): amazon/ebay/craigslist/youtube/twitter/reddit/linkedin searches
are not exposed — legacy impls are broken or stubbed. `send_email`,
`calendar_events`, `stock_price`, `crypto_price`, `currency_convert`,
`get_weather`, `translate_text` were ghost names for real functions
(`google_finance`, `google_weather`, `google_translate`) and only the
real names are exposed. `download_youtube`, `extract_video_clip`,
`fetch_emails`, and the persistent-shell variant are side-effectful and
postponed to a later PR.

Not changed: legacy `tools/*` modules are untouched; `server.py` dispatch
still runs the old tables for the surviving (non-ghost) tools until PR 4
wires in the registry-based dispatcher.

## Key design decisions (locked after PR 0 discussion)

1. **Single process** with per-tool timeouts, per-call try/except.
2. **BM25 memory search** by default; vector search becomes an optional
   `[memory-vec]` extra in a later PR.
3. **Self-summarization via LM Studio** on by default for oversized
   outputs, with a `raw=True` per-call escape hatch.
4. **Cross-platform**, but only where LM Studio runs the same way
   (desktop Windows / macOS / Linux). Headless Linux, containers, and
   mobile are out of scope.

## References

- MCP best practices: <https://modelcontextprotocol.info/docs/best-practices/>
- Tool-design guide (Phil Schmid): <https://www.philschmid.de/mcp-best-practices>
- LM Studio MCP host docs: <https://lmstudio.ai/docs/app/mcp>
- LM Studio context length API: <https://lmstudio.ai/docs/python/model-info/get-context-length>
- AWS Labs MCP server design guide: <https://github.com/awslabs/mcp/blob/main/DESIGN_GUIDELINES.md>
