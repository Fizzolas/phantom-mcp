"""
phantom.memory — durable, scoped memory for the running model.

Why this is separate from the legacy memory/ package:
  * Scope clarity. Phantom owns "desktop & environment state" memory:
    facts the model learned about THIS PC, action traces, task progress.
    If you happen to run another MCP server with its own memory store,
    we do not auto-sync between the two; phantom is self-contained.
  * Context-window awareness. Every read returns at most a few KB and
    is shaped by a TokenBudget so it cannot blow the LM Studio context.
  * Self-learning. action_trace records each tool call with outcome,
    args, and a reason; learn() summarizes failure patterns into
    "lessons" the model can read at boot.
  * No infinite history. Old traces auto-compact via a windowed
    summarization step that uses the LM Studio model itself if reachable.

Layout on disk (under data/phantom_memory/):
  facts.json     — durable key/value learned-about-environment facts
  tasks.json     — current and recent task records (goal, steps, status)
  traces.jsonl   — append-only action traces (tool, args_summary, outcome, ts)
  lessons.json   — distilled learnings ("when X fails, try Y first")
  notes/<id>.md  — large free-form notes the model wrote (chunked retrieval)

All paths are configurable via PhantomMemory(data_dir=...).
"""
from phantom.memory.store import PhantomMemory, MemoryConfig

__all__ = ["PhantomMemory", "MemoryConfig"]
