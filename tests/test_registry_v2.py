"""Smoke tests for the expanded phantom registry (post-overhaul)."""
from __future__ import annotations

import pytest

import phantom.tools  # ensure all @tool modules are imported
from phantom.tools._base import registry
from phantom.runtime.capabilities import probe_capabilities


def test_core_categories_registered():
    """Every category we promised in the overhaul must show up."""
    cats = {s.category for s in registry.all()}
    expected = {"memory", "files", "shell", "processes", "windows", "desktop", "system", "clipboard"}
    missing = expected - cats
    assert not missing, f"missing categories: {missing}"


def test_desktop_naming_convention_holds():
    """All input/vision tools should be desktop_*."""
    desktop_tools = [s.name for s in registry.all() if s.category == "desktop"]
    bad = [n for n in desktop_tools if not n.startswith("desktop_")]
    assert not bad, f"non-conforming desktop tool names: {bad}"


def test_memory_tools_namespaced():
    """All memory tools should be memory_* to avoid cross-server collisions."""
    mem_tools = [s.name for s in registry.all() if s.category == "memory"]
    bad = [n for n in mem_tools if not n.startswith("memory_")]
    assert not bad, f"non-conforming memory tool names: {bad}"


def test_capability_probe_runs_clean():
    caps = probe_capabilities()
    assert isinstance(caps, set)
    # OS tag is always emitted
    assert any(c.startswith("os:") for c in caps)


def test_available_subset_of_all_after_capability_set():
    caps = probe_capabilities()
    registry.set_capabilities(caps)
    avail = {s.name for s in registry.available()}
    every = {s.name for s in registry.all()}
    assert avail.issubset(every)
    # memory + files should always be available — no caps required
    for name in ("memory_fact_set", "file_read", "shell_python"):
        assert name in avail


@pytest.mark.asyncio
async def test_registry_dispatch_round_trip_for_memory_fact():
    """End-to-end: register-call-result envelope."""
    res = await registry.call("memory_fact_set", {"key": "test_smoke", "value": "hello"})
    assert res.ok is True
    res2 = await registry.call("memory_fact_get", {"key": "test_smoke"})
    assert res2.ok and res2.data["value"] == "hello"
    # cleanup
    await registry.call("memory_fact_delete", {"key": "test_smoke"})


@pytest.mark.asyncio
async def test_registry_validates_args():
    res = await registry.call("memory_fact_set", {"key": ""})  # missing value, key blank
    assert res.ok is False
    assert res.meta["category"] == "client_error"
