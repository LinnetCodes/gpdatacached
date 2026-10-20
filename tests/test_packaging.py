"""PEP 561 packaging guarantees.

The library ships inline type annotations; the ``py.typed`` marker makes them
visible to downstream type checkers (mypy / pyright). Without it, strict-mode
consumers get ``Any`` for the whole API surface and resort to
``ignore_missing_imports`` overrides, which silently disables checking.
"""

from __future__ import annotations

import importlib.resources


def test_ships_py_typed_marker() -> None:
    marker = importlib.resources.files("gpdatacached").joinpath("py.typed")
    assert marker.is_file(), "PEP 561 py.typed marker must ship inside the package"
