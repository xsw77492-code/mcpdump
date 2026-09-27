"""mcpdump -- a terminal toolchain for MCP servers.

Three design principles, none of which any change may violate:
1. Zero-dependency wire layer: JSON-RPC and the transports are implemented here,
   with no MCP SDK. Seeing the actual wire traffic is the product's core value, so
   the wire level must be fully under our control.
2. Terminal first: every capability must work in a terminal before any GUI.
3. Scriptable: every command supports --json and can run in CI.
"""

__version__ = "0.1.0"

#: Protocol version this client offers by default.
#: The server returns the version it actually supports in the initialize response,
#: and the session adopts the server's value.
DEFAULT_PROTOCOL_VERSION = "2025-06-18"

#: Known MCP protocol versions, used by conformance checks to tell whether the
#: version a server returns is in the known set.
KNOWN_PROTOCOL_VERSIONS = (
    "2024-11-05",
    "2025-03-26",
    "2025-06-18",
)

__all__ = ["__version__", "DEFAULT_PROTOCOL_VERSION", "KNOWN_PROTOCOL_VERSIONS"]
