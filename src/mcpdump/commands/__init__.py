"""Command layer: one module per subcommand.

This package deliberately declares no ``__all__``: it imports no submodules, so
names listed there would raise ``AttributeError`` on ``import *``. Promising a
contract you cannot honour is worse than promising nothing. ``cli.py`` imports
submodules explicitly (``from .commands import call as call_cmd``), so which
commands exist is visible at the registration site.
"""
