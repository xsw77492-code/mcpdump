"""Wire-layer unit tests. This layer performs no I/O, so it must be covered fully."""

from __future__ import annotations

import json

import pytest

from mcpdump.core.jsonrpc import (
    JsonRpcError,
    ProtocolError,
    decode,
    encode_notification,
    encode_request,
    is_notification,
    is_request,
    is_response,
    unwrap,
)


def test_encode_request_omits_params_when_none() -> None:
    line = encode_request(1, "tools/list")
    assert json.loads(line) == {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}


def test_encode_request_keeps_params_when_empty_dict() -> None:
    line = encode_request(2, "tools/list", {})
    assert json.loads(line)["params"] == {}


def test_encode_uses_compact_separators() -> None:
    """MCP frames are newline-delimited, so compact separators cut transfer volume."""
    assert " " not in encode_request(1, "ping", {"a": 1})


def test_encode_keeps_non_ascii_readable() -> None:
    """Non-ASCII arguments must not be escaped to \\uXXXX, or --trace output is unreadable."""
    line = encode_request(1, "echo", {"text": "你好"})
    assert "你好" in line


def test_encode_notification_has_no_id() -> None:
    payload = json.loads(encode_notification("notifications/initialized"))
    assert "id" not in payload
    assert payload["method"] == "notifications/initialized"


def test_decode_rejects_non_json() -> None:
    with pytest.raises(ProtocolError, match="Not valid JSON"):
        decode("这不是 JSON")


def test_decode_rejects_non_object() -> None:
    with pytest.raises(ProtocolError, match="must be a JSON object"):
        decode("[1, 2, 3]")


def test_decode_rejects_missing_version() -> None:
    with pytest.raises(ProtocolError, match="jsonrpc"):
        decode('{"id":1,"result":{}}')


def test_decode_accepts_valid_message() -> None:
    assert decode('{"jsonrpc":"2.0","id":1,"result":{}}')["id"] == 1


def test_message_kind_detection() -> None:
    request = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    notification = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    response = {"jsonrpc": "2.0", "id": 1, "result": {}}

    assert is_request(request) and not is_notification(request)
    assert is_notification(notification) and not is_request(notification)
    assert is_response(response)


def test_unwrap_returns_result() -> None:
    assert unwrap({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}) == {"ok": True}


def test_unwrap_raises_on_error() -> None:
    with pytest.raises(JsonRpcError) as info:
        unwrap(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": -32602, "message": "参数错误", "data": {"field": "a"}},
            }
        )
    assert info.value.code == -32602
    assert info.value.message == "参数错误"
    assert info.value.data == {"field": "a"}


def test_unwrap_tolerates_error_without_data() -> None:
    with pytest.raises(JsonRpcError) as info:
        unwrap({"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "x"}})
    assert info.value.data is None
