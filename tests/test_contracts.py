"""Unit tests for the ToolResult envelope and error classifier."""
from __future__ import annotations

from phantom.contracts import ToolResult, ok, fail, classify, ErrorCategory


def test_ok_basic():
    r = ok({"hello": "world"})
    assert r.ok is True
    assert r.data == {"hello": "world"}
    assert r.error is None
    d = r.to_dict()
    assert d["ok"] is True
    assert d["data"] == {"hello": "world"}


def test_ok_with_meta():
    r = ok("payload", source="test", elapsed_ms=12)
    assert r.meta == {"source": "test", "elapsed_ms": 12}


def test_fail_default_category():
    r = fail("it broke")
    assert r.ok is False
    assert r.error == "it broke"
    assert r.meta["category"] == "server_error"


def test_fail_with_hint():
    r = fail("network down", hint="retry later", category="external_error")
    assert r.hint == "retry later"
    assert r.meta["category"] == "external_error"


def test_classify_client_error():
    assert classify(ValueError("bad")) == ErrorCategory.CLIENT_ERROR
    assert classify(FileNotFoundError()) == ErrorCategory.CLIENT_ERROR
    assert classify(PermissionError()) == ErrorCategory.CLIENT_ERROR


def test_classify_external_error():
    assert classify(TimeoutError()) == ErrorCategory.EXTERNAL_ERROR
    assert classify(ConnectionError()) == ErrorCategory.EXTERNAL_ERROR


def test_classify_unknown_is_server():
    class WeirdError(Exception):
        pass

    assert classify(WeirdError()) == ErrorCategory.SERVER_ERROR
