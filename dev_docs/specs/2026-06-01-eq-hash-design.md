# `__eq__` / `__hash__` Design

**Date**: 2026-06-01
**Issue**: #1 — 缺失的协议方法 (`__eq__`, `__hash__`)
**Priority**: P1

## Decision

Identity-based comparison, explicit unhashable declaration.

## `__eq__` Semantics

- Two GPDC objects are equal iff they share the same domain + namespace + canonical_name
- No Redis reads, O(1)
- Returns `NotImplemented` when compared with non-GPDC types (lets Python try the other side)
- Implemented in `GPDCDataObject` base class — all subclasses inherit

## `__hash__` Semantics

- `__hash__ = None` on `GPDCDataObject` — all GPDC objects are unhashable
- Follows Python convention: mutable objects must not be hashable
- This is already the default behavior for `SetObject` (via `MutableSet`), now made explicit

## Documentation

Base class docstring will note:
- `__eq__` uses identity (same canonical Redis key), not value comparison
- For value comparison, use `obj.value == python_value`

## Scope

| Change | File |
|---|---|
| Add `__eq__` and `__hash__ = None` to `GPDCDataObject` | `objects.py` |
| Update base class docstring | `objects.py` |
| Add tests for `__eq__` and `__hash__` | `tests/` |
