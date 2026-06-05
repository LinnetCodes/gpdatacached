# Remaining Issues B-F Fix Design

Date: 2026-06-06
Source: `dev_docs/2026-06-06-remaining-issues-bf-analysis.md`
Branch: `lifecycle-redesign-refactor`
Status: Design approved

---

## Overview

Fix 5 independent issues (B through F) identified in the BF analysis. All are low-complexity, localized fixes with clear boundaries. Total change: ~25-30 lines of production code + ~10 tests.

## Issues

### B — `meta.setter` missing P10 permission check

**File**: `src/gpdatacached/objects.py` (lines 83-88)

`meta.setter` calls `set_lifecycle()` without `_check_owner_permission`. All 5 other lifecycle-modifying paths include this check.

**Fix**: Add permission check before calling `set_lifecycle`:

```python
@meta.setter
def meta(self, new_meta: ObjectMeta) -> None:
    from .ttl import policy_from_meta
    ctx = self._load_lifecycle_context()
    self._check_owner_permission(ctx.meta)
    policy = policy_from_meta(new_meta)
    self.set_lifecycle(policy)
```

**Tests**: 1 test — anonymous object setting lifecycle via `meta.setter` should raise `LifecyclePermissionError`.

---

### C — Config allows `cache_mode="ttl"` with `ttl=0`

**File**: `src/gpdatacached/config.py`

`GPDCDomainConfig` and `GPDCNamespaceConfig` validate fields independently but lack cross-field validation. `cache_mode="ttl"` + `ttl=0` passes but is semantically invalid.

**Fix**: Add `@model_validator(mode="after")` to both config classes:

- `GPDCDomainConfig`: reject when `default_cache_mode in ("ttl", "sliding")` and `default_ttl_seconds <= 0`
- `GPDCNamespaceConfig`: reject when `default_cache_mode in ("ttl", "sliding")` and `default_ttl_seconds is not None` and `default_ttl_seconds <= 0`

**Tests**: 2-3 tests — invalid combinations raise `ValidationError`; valid ones pass.

---

### D — ListObject slice error unclear

**File**: `src/gpdatacached/container.py`

`_normalize_index` receives `slice` objects without type checking, producing `TypeError: '<' not supported between instances of 'slice' and 'int'`.

**Fix**: Add slice type check in `_normalize_index` with clear error message:

```python
def _normalize_index(self, index, message="list index out of range"):
    if isinstance(index, slice):
        raise TypeError(
            "ListObject does not support slice indexing; "
            "use list(obj)[start:stop] instead"
        )
    ...
```

This single check covers all 3 callers (`__getitem__`, `__setitem__`, `_delete_index`).

**Tests**: 3 tests — `lst[1:3]`, `lst[1:3] = ...`, `del lst[1:3]` each raise clear `TypeError`.

---

### E — Error message missing `canonical_uri`

**File**: `src/gpdatacached/exceptions.py` (lines 24-27)

`CanonicalObjectReferencedError` stores `canonical_uri` but omits it from the error message.

**Fix**: Include `canonical_uri` in the message string:

```python
super().__init__(
    f"Cannot delete canonical object {canonical_name!r} ({canonical_uri}) "
    f"because it is still referenced by: [{names}]"
)
```

**Tests**: Update existing tests matching the old message string.

---

### F — GC set ref dead code (delete, not comment)

**Files**: `src/gpdatacached/gc.py` (lines 161-163), `src/gpdatacached/keys.py` (lines 116-118)

`collection:set` branches for ref scanning are unreachable because `encode_key()` rejects container types, so sets can never contain `ref:`-encoded values.

**Fix**: Remove the `collection:set` branches from both files. Add comments explaining sets don't support container references.

**Tests**: No new tests needed (removing unreachable code). Existing GC/cascade tests must pass.

---

## Implementation Order

All 5 issues are independent and can be implemented in parallel:
B -> C -> D -> E -> F

## Test Files

| Issue | Test File |
|-------|-----------|
| B | `tests/test_objects_lifecycle.py` |
| C | `tests/test_config.py` |
| D | `tests/test_objects_container.py` |
| E | `tests/test_exceptions.py` |
| F | No new tests |
