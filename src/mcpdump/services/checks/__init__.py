"""Registry of conformance checks.

Adding a check means adding a file: drop a module into ``checks/`` exporting
``CHECKS = (YourCheck(),)`` and the registry picks it up.

The filename prefix is the execution order, not decoration: ``10_stdout_purity``
must run last because it reads the stdout violations accumulated over the whole
session.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Iterator

from .base import (
    BaseCheck,
    Check,
    CheckContext,
    CheckResult,
    Finding,
    RawProbe,
    Severity,
    Status,
    error,
    json_evidence,
    note,
    warn,
)

__all__ = [
    "BaseCheck",
    "Check",
    "CheckContext",
    "CheckResult",
    "Finding",
    "RawProbe",
    "Severity",
    "Status",
    "all_checks",
    "check_by_id",
    "error",
    "json_evidence",
    "note",
    "warn",
]

#: Modules that are not checks: the base class and private modules.
_SKIPPED_MODULES = frozenset({"base"})


def _check_modules() -> Iterator[str]:
    """Walk the check modules under ``checks/`` in filename order."""
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda item: item.name):
        if info.name in _SKIPPED_MODULES or info.name.startswith("_"):
            continue
        yield info.name


def all_checks() -> tuple[Check, ...]:
    """Load every check, in filename order."""
    loaded: list[Check] = []
    for name in _check_modules():
        module = importlib.import_module(f"{__name__}.{name}")
        loaded.extend(getattr(module, "CHECKS", ()))
    return tuple(loaded)


def check_by_id(check_id: str) -> Check | None:
    """Look up a check by id, for tests and a future ``--only``."""
    for check in all_checks():
        if check.id == check_id:
            return check
    return None
