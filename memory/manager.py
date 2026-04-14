"""
Persistent memory manager.
- Key-value store saved to data/memory.json
- Conversation compression using LM Studio's local API
"""
import json, difflib, asyncio, httpx
from pathlib import Path

LMS_BASE = "http://localhost:1234/v1"   # LM Studio default

class MemoryManager:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.data_dir.mkdir(exist_ok=True)
        self._path = data_dir / "memory.json"
        self._store: dict = self._load()

    def _load(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text())
            except Exception:
                return {}
        return {}

    def _persist(self):
        self._path.write_text(json.dumps(self._store, indent=2, ensure_ascii=False))

    def save(self, key: str, value: str) -> str:
        self._store[key] = value
        self._persist()
        return f"Memory saved: [{key}]"

    def get(self, key: str) -> str:
        return self._store.get(key, f"No memory found for key: '{key}'")

    def list_keys(self) -> list:
        return sorted(self._store.keys())

    def search(self, query: str) -> list:
        results = []
        q = query.lower()
        for k, v in self._store.items():
            combined = f"{k} {v}".lower()
            ratio = difflib.SequenceMatcher(None, q, combined).ratio()
            if ratio > 0.3 or q in combined:
                results.append({"key": k, "value": v[:300], "score": round(ratio, 2)})
        return sorted(results, key=lambda x: x["score"], reverse=True)[:10]

    async def compress(self, conversation: str, label: str) -> str:
        """
        Send conversation to LM Studio for summarization,
        then store the summary as a memory entry.
        Falls back to a raw excerpt if LM Studio is unreachable.
        """
        summary = await self._call_lms_summarize(conversation)
        self._store[f"compressed:{label}"] = summary
        self._persist()
        return f"Compressed '{label}' into memory. Summary: {summary[:200]}..."

    async def _call_lms_summarize(self, text: str) -> str:
        prompt = (
            "Summarize the following conversation into a compact, factual memory "
            "digest under 300 words. Preserve key decisions, facts, and context.\n\n"
            f"CONVERSATION:\n{text[:6000]}"
        )
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{LMS_BASE}/chat/completions",
                    json={
                        "model": "local-model",
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 400,
                        "temperature": 0.3,
                    }
                )
                data = resp.json()
                return data["choices"][0]["message"]["content"]
        except Exception:
            return f"[RAW EXCERPT] {text[:500]}"
