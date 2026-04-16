"""
scripts/gen_docs.py — regenerate README tool table + SYSTEM_PROMPT catalog
from the live tool registry.

Run after adding/removing/renaming any tool:

    python scripts/gen_docs.py

What it writes (under docs/generated/)
  tool-catalog.md       Markdown table: name | needs | timeout | summary,
                        grouped by category. Human-readable reference.
  tool-system-prompt.md Model-facing catalog: bullet per tool with the
                        first line of the docstring and required/optional
                        args. Feed into the LM Studio system prompt when
                        the registry-based dispatcher lands in PR 4.

Why generate it
  The docs and the code used to drift (ghost names like stock_price,
  crypto_price, etc. were in the old SYSTEM_PROMPT but had no impl). Now
  the registry IS the source of truth. If a tool doesn't register, it
  doesn't appear in docs. The hand-written README.md and SYSTEM_PROMPT.md
  at the repo root still describe the legacy dispatcher and will be
  replaced by these generated files in PR 4.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make the repo importable when run as a script.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import phantom.tools  # noqa: F401 — side effect: populates registry
from phantom.tools import registry
from phantom.tools._base import ToolSpec


def _first_line(doc: str) -> str:
    """Return the first non-empty line of a docstring, or ''."""
    for line in (doc or "").splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _required_args(spec: ToolSpec) -> list[str]:
    """Required fields from the pydantic schema, in declaration order."""
    if spec.schema is None:
        return []
    js = spec.json_schema()
    req = js.get("required") or []
    # Preserve property order when possible.
    props = list((js.get("properties") or {}).keys())
    ordered = [p for p in props if p in req]
    for r in req:
        if r not in ordered:
            ordered.append(r)
    return ordered


def _optional_args(spec: ToolSpec) -> list[str]:
    if spec.schema is None:
        return []
    js = spec.json_schema()
    req = set(js.get("required") or [])
    props = list((js.get("properties") or {}).keys())
    return [p for p in props if p not in req]


def gen_readme_table(specs: list[ToolSpec]) -> str:
    """Group tools by category, emit one Markdown table per category."""
    by_cat: dict[str, list[ToolSpec]] = {}
    for s in specs:
        by_cat.setdefault(s.category, []).append(s)

    lines: list[str] = [
        "# Phantom-MCP Tool Catalog",
        "",
        "This file is generated from the live tool registry.",
        "Do not edit by hand — run `python scripts/gen_docs.py` instead.",
        "",
        f"**Total tools:** {len(specs)} across {len(by_cat)} categories.",
        "",
    ]

    for cat in sorted(by_cat):
        tools = sorted(by_cat[cat], key=lambda t: t.name)
        lines.append(f"## {cat}")
        lines.append("")
        lines.append("| Tool | Needs | Timeout | Summary |")
        lines.append("| ---- | ----- | ------- | ------- |")
        for s in tools:
            needs = ", ".join(s.needs) if s.needs else "—"
            summary = _first_line(s.description).replace("|", r"\|")
            lines.append(f"| `{s.name}` | {needs} | {s.timeout_s:g}s | {summary} |")
        lines.append("")

    return "\n".join(lines) + "\n"


def gen_system_prompt(specs: list[ToolSpec]) -> str:
    """Model-facing tool catalog — one bullet per tool, compact."""
    by_cat: dict[str, list[ToolSpec]] = {}
    for s in specs:
        by_cat.setdefault(s.category, []).append(s)

    lines: list[str] = [
        "# Phantom-MCP System Prompt — Tool Catalog",
        "",
        "You have access to the tools below. Each tool returns a structured",
        "`ToolResult` envelope: `{ok: bool, data|error, hint, category}`.",
        "On failure, read `hint` and either retry with corrected arguments",
        "or pick a different tool. Never assume a tool succeeded without",
        "checking `ok`.",
        "",
        "Tools marked `[desktop]` or `[display]` run on the user's machine;",
        "they may not be available in headless environments. Tools marked",
        "`[playwright]` require a browser runtime.",
        "",
    ]

    for cat in sorted(by_cat):
        tools = sorted(by_cat[cat], key=lambda t: t.name)
        lines.append(f"## {cat}")
        lines.append("")
        for s in tools:
            badges = "".join(f"[{n}]" for n in s.needs)
            req = _required_args(s)
            opt = _optional_args(s)
            sig_bits = [f"`{r}`" for r in req] + [f"`{o}?`" for o in opt]
            sig = ", ".join(sig_bits) if sig_bits else "no args"
            summary = _first_line(s.description)
            prefix = f"- **{s.name}**"
            if badges:
                prefix += f" {badges}"
            lines.append(f"{prefix} — {summary}")
            lines.append(f"  - args: {sig}")
        lines.append("")

    return "\n".join(lines) + "\n"


def main() -> int:
    specs = registry.all()
    if not specs:
        print("ERROR: registry is empty — did tool modules fail to import?",
              file=sys.stderr)
        return 1

    readme = gen_readme_table(specs)
    prompt = gen_system_prompt(specs)

    out_dir = ROOT / "docs" / "generated"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "tool-catalog.md").write_text(readme, encoding="utf-8")
    (out_dir / "tool-system-prompt.md").write_text(prompt, encoding="utf-8")

    print(f"wrote docs/generated/tool-catalog.md       ({len(readme):,} chars)")
    print(f"wrote docs/generated/tool-system-prompt.md ({len(prompt):,} chars)")
    print(f"catalog: {len(specs)} tools")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
