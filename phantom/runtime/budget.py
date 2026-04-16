"""
Token budget manager.

Purpose:
  Prevent phantom from handing the LM Studio-loaded model an output that
  blows the context window. This is the "context economy" rule from the
  refactor plan — tool names, descriptions, and outputs all compete for
  the same tokens.

Policy (defaults, overridable):
  * Reserve 20% of context for system prompt / tool schemas.
  * Reserve 40% for the running conversation.
  * Leave ~40% for tool outputs — split across however many tool calls
    happen between model turns. We cap a single tool output at
    `per_call_ratio` of the total tool budget (default 0.25).

Token estimation uses a cheap heuristic (chars/4) by default. If
`tiktoken` is importable we use it for a more accurate count. Either way
the number is a rough bound — better to undershoot than to OOM the model.

The budget does NOT silently drop data. It truncates with a trailing
sentinel so the model knows more exists:

    "...<output truncated: 4821 of 18400 chars shown; call with
     raw=True or narrow your query>..."
"""
from __future__ import annotations

from dataclasses import dataclass

# Rough heuristic when tiktoken isn't available. 4 chars/token is the
# OpenAI rule-of-thumb; local models vary but this keeps us safe-ish.
CHARS_PER_TOKEN_HEURISTIC = 4


@dataclass
class TokenBudget:
    context_length: int
    system_reserve_ratio: float = 0.20
    conversation_reserve_ratio: float = 0.40
    per_call_ratio: float = 0.25

    @property
    def tool_output_pool(self) -> int:
        remaining = 1.0 - self.system_reserve_ratio - self.conversation_reserve_ratio
        return max(512, int(self.context_length * remaining))

    @property
    def per_call_tokens(self) -> int:
        return max(256, int(self.tool_output_pool * self.per_call_ratio))

    @property
    def per_call_chars(self) -> int:
        return self.per_call_tokens * CHARS_PER_TOKEN_HEURISTIC

    # ------------------------------------------------------------------
    # Token estimation
    # ------------------------------------------------------------------

    def estimate_tokens(self, text: str) -> int:
        if not text:
            return 0
        try:
            import tiktoken  # type: ignore

            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except Exception:
            return max(1, len(text) // CHARS_PER_TOKEN_HEURISTIC)

    # ------------------------------------------------------------------
    # Truncation
    # ------------------------------------------------------------------

    def fit(self, text: str) -> tuple[str, bool, int]:
        """
        Fit `text` into per_call_chars.
        Returns (possibly_truncated_text, was_truncated, original_char_count).
        """
        if text is None:
            return "", False, 0
        original = len(text)
        if original <= self.per_call_chars:
            return text, False, original

        sentinel = (
            f"\n\n...<output truncated by phantom budget: showing "
            f"{self.per_call_chars:,} of {original:,} chars; "
            f"re-run with a narrower query, pagination args, or raw=True>"
        )
        kept = self.per_call_chars - len(sentinel)
        if kept < 256:
            kept = 256
        return text[:kept] + sentinel, True, original

    def fit_any(self, value):
        """
        Apply fit() to strings, recurse into dict/list leaves.
        Non-string leaves are passed through.
        Returns (value, truncated_flag).
        """
        truncated = False
        if isinstance(value, str):
            new, was, _ = self.fit(value)
            return new, was
        if isinstance(value, list):
            out = []
            for item in value:
                v, t = self.fit_any(item)
                out.append(v)
                truncated = truncated or t
            return out, truncated
        if isinstance(value, dict):
            out = {}
            for k, v in value.items():
                nv, t = self.fit_any(v)
                out[k] = nv
                truncated = truncated or t
            return out, truncated
        return value, False
