"""
Chunker — splits large text into numbered chunks that fit inside context limits.

Context math (conservative, safe for Gemma 4 E4B at 32k context):
  32768 tokens total
  - ~4000  system prompt
  - ~2500  screenshot (if used)
  - ~2000  tool overhead / recent turns
  = ~24000 tokens free for content
  At ~3.5 chars/token that's ~84,000 chars safe budget.
  We use CHUNK_SIZE = 6000 chars (~1700 tokens) per chunk so the model can
  load several chunks at once without blowing context.

Usage:
  manifest = split_text(text, label)        -> dict with chunk_ids list
  text     = reassemble(manifest, mem)      -> full text back
  preview  = chunk_summary(manifest)        -> one-liner for each chunk
"""

from __future__ import annotations
import hashlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memory.manager import MemoryManager

# Characters per chunk. At ~3.5 chars/token this is ~1700 tokens.
# Raise to 10000 if you bump LM Studio context to 65536.
CHUNK_SIZE = 6000


def _chunk_key(label: str, idx: int) -> str:
    return f"chunk:{label}:{idx:04d}"


def split_and_store(text: str, label: str, mem: "MemoryManager") -> dict:
    """
    Split text into CHUNK_SIZE pieces, store each in mem under
    chunk:<label>:<index>, then store a manifest under chunk:manifest:<label>.
    Returns the manifest dict.
    """
    chunks = [text[i:i + CHUNK_SIZE] for i in range(0, len(text), CHUNK_SIZE)]
    chunk_ids = []
    for idx, piece in enumerate(chunks):
        key = _chunk_key(label, idx)
        mem.raw_set(key, piece)
        chunk_ids.append(key)

    manifest = {
        "label": label,
        "total_chars": len(text),
        "chunk_count": len(chunks),
        "chunk_size": CHUNK_SIZE,
        "chunk_ids": chunk_ids,
        "checksum": hashlib.md5(text.encode()).hexdigest(),
    }
    mem.raw_set(f"chunk:manifest:{label}", manifest)
    return manifest


def load_chunk(label: str, index: int, mem: "MemoryManager") -> str:
    """Load a single chunk by label and 0-based index."""
    key = _chunk_key(label, index)
    val = mem.raw_get(key)
    if val is None:
        return f"[ERROR] Chunk not found: {key}"
    return val


def reassemble(label: str, mem: "MemoryManager") -> str:
    """Reassemble all chunks for a label back into the original text."""
    manifest = mem.raw_get(f"chunk:manifest:{label}")
    if manifest is None:
        return f"[ERROR] No chunk manifest found for label: '{label}'"
    parts = []
    for key in manifest["chunk_ids"]:
        piece = mem.raw_get(key)
        if piece is None:
            parts.append(f"[MISSING CHUNK: {key}]")
        else:
            parts.append(piece)
    return "".join(parts)


def get_manifest(label: str, mem: "MemoryManager") -> dict | None:
    return mem.raw_get(f"chunk:manifest:{label}")


def list_chunk_labels(mem: "MemoryManager") -> list[str]:
    """Return all labels that have a stored manifest."""
    prefix = "chunk:manifest:"
    return [
        k[len(prefix):]
        for k in mem.raw_keys()
        if k.startswith(prefix)
    ]


def delete_chunks(label: str, mem: "MemoryManager") -> int:
    """Delete all chunks and manifest for a label. Returns count deleted."""
    manifest = mem.raw_get(f"chunk:manifest:{label}")
    deleted = 0
    if manifest:
        for key in manifest["chunk_ids"]:
            if mem.raw_delete(key):
                deleted += 1
    if mem.raw_delete(f"chunk:manifest:{label}"):
        deleted += 1
    return deleted
