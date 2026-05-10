# GPDCNamespace Value-Semantics Refactor

**Date:** 2026-05-10

## Problem

GPDCNamespace's `__setitem__()` accepts Python native values and auto-creates GPDCScalarObject instances, but `__getitem__()` returns GPDCScalarObject instances rather than Python native values. This breaks the expected symmetry of `ns[key] = value; assert ns[key] == value`.

Additionally, the inherited `MutableMapping.setdefault()` has an asymmetry: when the key exists it returns a GPDCScalarObject (via `__getitem__`), when missing it returns the raw Python default.

## Design

### Semantic Model

| Method | Operates on | Returns |
|--------|------------|---------|
| `__setitem__(key, value)` | Python value | `None` |
| `__getitem__(key)` | Python value | Python native value |
| `setdefault(key, default)` | Python value | Python native value (inherited) |
| `get_object(key, [default])` | GPDCScalarObject | GPDCScalarObject instance |

### Changes to `namespace.py`

#### 1. New internal helper `_get_object(key)`

Extracts current `__getitem__` logic (validate key, read type from Redis, instantiate GPDCScalarObject subclass). Returns a GPDCScalarObject instance. Raises `KeyError` if not found.

#### 2. New `__getitem__(key)`

```python
def __getitem__(self, key: str):
    return self._get_object(key).value
```

Returns a Python native value.

#### 3. New public method `get_object(key, default=_SENTINEL)`

```python
_SENTINEL = object()

def get_object(self, key: str, default=_SENTINEL):
    try:
        return self._get_object(key)
    except KeyError:
        if default is _SENTINEL:
            raise
        self[key] = default
        return self._get_object(key)
```

- `default` accepts both Python native values and GPDCScalarObject instances (mirrors `__setitem__` unwrapping behavior).
- If key missing and default provided: creates the object via `__setitem__`, then returns the GPDCScalarObject via `_get_object`.

#### 4. `setdefault()` — no explicit override

The inherited `MutableMapping.setdefault` calls our `__getitem__` and `__setitem__`, which now both operate on Python native values. The result is always a Python native value, symmetric in both branches.

### Invariants

- Round-trip: `ns[key] = value; assert ns[key] == value` for all supported types
- `ns.get_object(key).value == ns[key]`
- All MutableMapping methods (`.get()`, `.values()`, `.items()`, `.pop()`) delegate to `__getitem__` and return native values
- `get_object(key)` without default raises `KeyError` when key is missing
- `get_object(key, default)` creates and returns a GPDCScalarObject when key is missing

### Test Impact

- `TestNamespaceGetItem` — update assertions: `ns["x"]` returns `42` (int), not `IntObject`
- `TestNamespaceMutableMapping.test_setdefault_existing` — now returns native value
- `TestNamespaceMutableMapping.test_setdefault_missing` — already returns native value, should still pass
- New tests for `get_object()`: key exists, key missing without default (KeyError), key missing with Python default, key missing with GPDCScalarObject default
- Integration tests: update patterns like `ns["x"].value` to just `ns["x"]`

### Files Changed

| File | Change |
|------|--------|
| `src/gpdatacached/namespace.py` | Refactor `__getitem__`, add `_get_object`, add `get_object` |
| `tests/test_namespace.py` | Update `TestNamespaceGetItem`, add `TestGetObjec` test class |
| `tests/test_integration.py` | Update assertions to use new value-level `__getitem__` |
| `tests/test_spec_verification.py` | Update assertions |
