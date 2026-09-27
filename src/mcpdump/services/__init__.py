"""Service layer: business decisions that are not about the protocol.

``core`` knows how to talk; ``services`` knows what to conclude once the talking
is done. ``report.py`` is the one module here that may touch ``ui`` (it is the
rendering adapter); the rest produce data structures and never print.
"""
