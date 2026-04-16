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
| 2   | Memory adapter + task state machine. Un-expose chunk_* tools.                    | **this PR** |
| 3   | Migrate remaining tools. Delete ghost modules. Regenerate README + SYSTEM_PROMPT. | pending |
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
