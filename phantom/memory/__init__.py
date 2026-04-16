"""
phantom.memory — thin, stable adapter over the legacy MemoryManager.

The goal here is NOT to reimplement storage. The legacy
memory/manager.py already has a working JSON-backed store with
facts / tasks / chunks / cache namespaces and an asyncio-locked writer.

What this adapter gives us:

  1. A stable, small surface the new @tool-registered memory tools can
     call without importing the legacy module directly at every site.
     Future PRs can swap the backend (sqlite, vector store, BM25 index)
     by editing this one file.

  2. Chunk storage becomes INTERNAL. In the legacy code, chunk_save /
     chunk_load / chunk_reassemble / chunk_list / chunk_delete were all
     advertised as tools. That's a bad idea: chunking is an
     implementation detail for handling oversized blobs. The new surface
     exposes `memory_save(key, value)` and handles chunking transparently
     for large values. One tool, not five.

  3. Task state machine gets a typed shape that the PR 4 planner can
     consume without grep-ing dicts.

  4. BM25 search — phantom.memory.search defaults to BM25 ranking over
     stored facts + task summaries. The legacy implementation used
     difflib.SequenceMatcher which is quadratic on long text. We keep
     the legacy fallback if rank_bm25 isn't installed.

All methods return plain Python types; the tool layer wraps them in
ToolResult envelopes.
"""
from phantom.memory.adapter import MemoryAdapter, get_memory

__all__ = ["MemoryAdapter", "get_memory"]
