# P0-3: Stale Alias Mapping Semantics Fix — Design Spec

## Problem

When a canonical owner expires or is deleted, its alias bindings (Redis strings with `ref:` prefix) remain in Redis. This causes an inconsistency in the namespace's public mapping interface:

| Method | Current Behavior | Bug? |
|--------|-----------------|------|
| `__getitem__` | Uses `_resolve_binding()` → raises `KeyError` for stale | No |
| `__contains__` | Checks `r.exists(obj_key)` only | **Yes** |
| `__iter__` | SCAN + filter anonymous only | **Yes** |
| `__len__` | Counts via `__iter__` | **Yes** |

Result: `"alias" in ns` is `True`, `list(ns)` contains `"alias"`, but `ns["alias"]` raises `KeyError`.

## Design Decision

Make `_resolve_binding()` the single source of truth for alias validity across all public mapping methods.

**Stale alias = "does not exist"** in all of `__getitem__`, `__contains__`, `__iter__`, `__len__`.

## Implementation Approach

### `__contains__` (namespace.py:473-479)

Replace the raw `exists()` check with a `_resolve_binding()` call. Return `True` only if the binding resolves successfully.

### `__iter__` (namespace.py:481-496)

After scanning keys and filtering anonymous names, filter each candidate through `_resolve_binding()`. Only yield names that resolve to valid bindings.

### `__len__` (namespace.py:498-504)

Already delegates to `__iter__`, so it auto-corrects once `__iter__` is fixed.

### `__getitem__` (namespace.py:368-374)

Already correct — uses `_get_object()` → `_resolve_binding()`. No change needed.

## Constraints

- No new public API.
- No side effects in read paths (no auto-cleanup of stale aliases).
- No changes to alias storage model.
- GC cleanup remains unchanged — it still cleans stale aliases when it runs.
- This fix makes GC non-essential for correctness of public semantics.

## Performance Consideration

`__iter__` currently does a single SCAN pass. The fix adds a `_resolve_binding()` call per scanned key. This means:
- Each alias key requires a `type` + potential `get` + `hgetall` Redis round trips.
- For owner keys, it requires `hgetall`.
- This is O(n) Redis calls where n = total keys in namespace.
- Acceptable because `__iter__` and `__len__` are already O(n) operations.
- For very large namespaces, a future optimization could batch-validate, but that's out of scope.

## Test Plan

Tests to add:

1. Owner expired/TTL-expired, alias still in Redis:
   - `ns["alias"]` → `KeyError`
   - `"alias" in ns` → `False`
   - `list(ns)` does not contain alias
   - `len(ns)` does not count alias

2. Normal alias (owner still alive):
   - All mapping methods work correctly (regression check)

3. Owner binding (not alias):
   - All mapping methods unaffected by the filtering logic

4. Multiple aliases, some stale, some valid:
   - `list(ns)` returns only valid aliases
   - `len(ns)` counts only valid aliases

Source design doc: `dev_docs/stale_alias_semantics_design.md`
