"""Exit code contract.

Public contract between the command layer and ``cli``: scripts and CI depend on
these numbers directly. Zero dependencies, so anything may import it without a
cycle risk.
"""

from __future__ import annotations

__all__ = [
    "EXIT_ENVIRONMENT",
    "EXIT_INTERRUPTED",
    "EXIT_NOT_CONFORMANT",
    "EXIT_OK",
    "EXIT_PROTOCOL",
    "EXIT_TIMEOUT",
    "EXIT_USAGE",
    "DOCUMENTED_CODES",
]

#: Success.
EXIT_OK = 0

#: Usage or input error: bad arguments, unknown tool name, unwritable file.
EXIT_USAGE = 1

#: The server returned a JSON-RPC error -- connected, but it rejected the request.
EXIT_PROTOCOL = 2

#: No response arrived. Distinct from "the server is gone", which is
#: ``core.transport.ServerGoneError``.
EXIT_TIMEOUT = 3

#: Environment error: executable not found, directory unreadable.
EXIT_ENVIRONMENT = 4

#: Interrupted by Ctrl-C. Follows the shell convention (128 + SIGINT) rather than
#: inventing another number.
EXIT_INTERRUPTED = 130

#: ``check`` only: non-conformant items found.
#:
#: Reuses 2 deliberately: to ``check``, "cannot connect" and "fails the health
#: check" are the same event -- this server is unusable right now.
EXIT_NOT_CONFORMANT = EXIT_PROTOCOL

#: Every legal value in the contract. Tests use it to assert that no command returns
#: an unplanned code.
DOCUMENTED_CODES = frozenset(
    {EXIT_OK, EXIT_USAGE, EXIT_PROTOCOL, EXIT_TIMEOUT, EXIT_ENVIRONMENT, EXIT_INTERRUPTED}
)
