"""
BM25 ranked search over the facts + tasks namespaces.

BM25 (Okapi BM25) is a classical bag-of-words ranker used by most
full-text search engines — see https://en.wikipedia.org/wiki/Okapi_BM25.
It needs no embedding model, no GPU, no network. Given phantom-mcp is
running locally alongside LM Studio, that's exactly the property we want.

If `rank_bm25` is importable, we use it. Otherwise we return an empty
list so the adapter falls back to the legacy difflib search.
"""
from __future__ import annotations

import re
from typing import Any


_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


def bm25_search(manager: Any, query: str, *, limit: int = 10) -> list[dict]:
    """
    Rank facts + task summaries against `query` with BM25.

    Returns a list of {namespace, key, preview, score} sorted by score desc.
    Returns [] if rank_bm25 isn't available; caller should fall back.
    """
    try:
        from rank_bm25 import BM25Okapi  # type: ignore
    except Exception:
        return []

    corpus_docs: list[str] = []
    corpus_meta: list[dict] = []

    # Pull raw facts and tasks out of the legacy manager.
    facts = manager._db.get("facts", {})
    for k, entry in facts.items():
        v = entry.get("value", "") if isinstance(entry, dict) else str(entry)
        corpus_docs.append(f"{k}\n{v}")
        corpus_meta.append({"namespace": "facts", "key": k, "preview": v[:300]})

    tasks = manager._db.get("tasks", {})
    for tid, task in tasks.items():
        blob = f"{tid}\n{task.get('goal', '')}\n{task.get('summary', '')}"
        corpus_docs.append(blob)
        corpus_meta.append(
            {
                "namespace": "tasks",
                "key": tid,
                "preview": task.get("goal", "")[:200],
                "status": task.get("status", "unknown"),
            }
        )

    if not corpus_docs:
        return []

    tokenized = [_tokenize(doc) for doc in corpus_docs]
    bm25 = BM25Okapi(tokenized)
    scores = bm25.get_scores(_tokenize(query))

    ranked = sorted(
        zip(scores, corpus_meta),
        key=lambda x: x[0],
        reverse=True,
    )

    # Filter out truly zero-score hits (query terms didn't appear at all).
    out: list[dict] = []
    for score, meta in ranked:
        if score <= 0:
            continue
        out.append({**meta, "score": round(float(score), 3)})
        if len(out) >= limit:
            break
    return out
