"""
Tool registry + @tool decorator.

This is the single piece that fixes the class of bugs uncovered by the
original review:

  1. Ghost modules
     A tool can only be registered by importing the module that defines
     it. If the import fails, _safe_import_tool_module() logs the failure
     and moves on — the tool simply doesn't appear in the registry.
     There is no way for dispatch to reference a tool that doesn't exist.

  2. Async-in-to_thread
     Registration captures inspect.iscoroutinefunction(fn). The executor
     branches on that flag, so async tools are awaited and sync tools are
     off-loaded to a worker thread — never the other way around.

  3. Schema drift
     The `schema` passed to @tool is a pydantic model. It is the single
     source of truth for both input validation at call time AND the JSON
     Schema advertised to the MCP client (LM Studio). One definition =
     no drift.

  4. Capability gating
     `needs=("playwright", "display", ...)` is checked against the boot
     probe. Tools whose dependencies aren't met are hidden from
     list_tools rather than blowing up at call time.
"""
from __future__ import annotations

import importlib
import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

try:
    from pydantic import BaseModel, ValidationError
except Exception:  # pragma: no cover — pydantic is a hard dep; guard anyway
    BaseModel = None  # type: ignore[assignment]
    ValidationError = Exception  # type: ignore[assignment,misc]

from phantom.contracts import ToolResult, fail
from phantom.runtime.executor import safe_call

log = logging.getLogger("phantom.registry")


@dataclass
class ToolSpec:
    name: str
    fn: Callable[..., Any] | Callable[..., Awaitable[Any]]
    schema: type | None
    category: str
    description: str
    needs: tuple[str, ...] = ()
    is_async: bool = False
    timeout_s: float = 30.0

    def json_schema(self) -> dict[str, Any]:
        """JSON Schema the MCP layer advertises for this tool."""
        if self.schema is None or BaseModel is None:
            return {"type": "object", "properties": {}, "additionalProperties": True}
        # pydantic v2 exposes .model_json_schema(); v1 exposes .schema()
        if hasattr(self.schema, "model_json_schema"):
            return self.schema.model_json_schema()
        return self.schema.schema()  # type: ignore[attr-defined]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._available_caps: set[str] = set()

    # -- registration -------------------------------------------------------

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            log.warning("tool %s re-registered; overwriting", spec.name)
        self._tools[spec.name] = spec

    def set_capabilities(self, caps: set[str]) -> None:
        """Call this once at boot with the capability probe result."""
        self._available_caps = set(caps)

    # -- introspection ------------------------------------------------------

    def all(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def available(self) -> list[ToolSpec]:
        """Tools whose `needs` are all satisfied by the current capabilities."""
        return [t for t in self._tools.values() if self._needs_met(t)]

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def _needs_met(self, spec: ToolSpec) -> bool:
        if not spec.needs:
            return True
        return all(need in self._available_caps for need in spec.needs)

    # -- dispatch -----------------------------------------------------------

    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        spec = self._tools.get(name)
        if spec is None:
            return fail(
                f"Unknown tool: {name!r}.",
                hint="Call list_tools to see currently available tools.",
                category="client_error",
            )

        if not self._needs_met(spec):
            missing = [n for n in spec.needs if n not in self._available_caps]
            return fail(
                f"{name} is unavailable on this system (missing: {', '.join(missing)}).",
                hint="Pick a different tool; the MCP host only advertises available ones.",
                category="client_error",
            )

        arguments = arguments or {}

        # Validate against pydantic schema when present.
        if spec.schema is not None and BaseModel is not None:
            try:
                model = spec.schema(**arguments)
                # pydantic v2 uses model_dump; v1 uses dict
                if hasattr(model, "model_dump"):
                    validated = model.model_dump()
                else:
                    validated = model.dict()  # type: ignore[attr-defined]
            except ValidationError as e:
                return fail(
                    f"Invalid arguments for {name}: {e!s}",
                    hint=f"See the tool's schema for valid fields.",
                    category="client_error",
                    validation_errors=str(e),
                )
        else:
            validated = arguments

        return await safe_call(
            spec.fn,
            kwargs=validated,
            is_async=spec.is_async,
            timeout_s=spec.timeout_s,
            tool_name=spec.name,
        )


# Module-level singleton. Tools register against this.
registry = ToolRegistry()


def tool(
    name: str,
    *,
    category: str,
    schema: type | None = None,
    needs: tuple[str, ...] = (),
    timeout_s: float = 30.0,
):
    """
    Decorator: register `fn` with the global registry.

    Usage:
        class ClipboardSetInput(BaseModel):
            text: str

        @tool("clipboard_set", category="clipboard", schema=ClipboardSetInput)
        def clipboard_set(text: str) -> str:
            ...
    """

    def wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
        spec = ToolSpec(
            name=name,
            fn=fn,
            schema=schema,
            category=category,
            description=(inspect.getdoc(fn) or "").strip(),
            needs=tuple(needs),
            is_async=inspect.iscoroutinefunction(fn),
            timeout_s=timeout_s,
        )
        registry.register(spec)
        return fn

    return wrap


def _safe_import_tool_module(mod_path: str) -> bool:
    """
    Import a tool module, swallowing ImportError/ModuleNotFoundError so a
    missing optional dep never crashes the server. Returns True on success.
    """
    try:
        importlib.import_module(mod_path)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("skipping tool module %s: %s", mod_path, e)
        return False
