"""Core layer: JSON-RPC codec, transports, MCP session, transparent proxy."""

from .jsonrpc import (
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
from .proxy import Direction, ProxyEvent, ProxyStats, StdioProxy
from .session import Exchange, MCPSession, ServerInfo, initialize_params
from .transport import (
    HttpTransport,
    ServerGoneError,
    StdioTransport,
    Transport,
    build_transport,
    split_command,
)

__all__ = [
    "JsonRpcError",
    "ProtocolError",
    "decode",
    "encode_notification",
    "encode_request",
    "is_notification",
    "is_request",
    "is_response",
    "unwrap",
    "Direction",
    "ProxyEvent",
    "ProxyStats",
    "StdioProxy",
    "Exchange",
    "MCPSession",
    "ServerInfo",
    "initialize_params",
    "HttpTransport",
    "ServerGoneError",
    "StdioTransport",
    "Transport",
    "build_transport",
    "split_command",
]
