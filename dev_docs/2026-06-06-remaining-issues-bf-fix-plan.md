# Remaining Issues B-F Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 5 independent low-severity issues (B through F) in the gpdatacached library.

**Architecture:** Each fix is a localized change to a single module (or pair of related modules for issue F). All fixes are independent and can be implemented in parallel by separate subagents. TDD approach: write test first, verify it fails, implement fix, verify it passes.

**Tech Stack:** Python 3, pytest, pydantic, redis

---

## File Structure

| Issue | Source File(s) | Test File |
|-------|---------------|-----------|
| B | `src/gpdatacached/objects.py:83-88` | `tests/test_objects_lifecycle.py` |
| C | `src/gpdatacached/config.py:1-62` | `tests/test_config.py` |
| D | `src/gpdatacached/container.py:202-208` | `tests/test_objects_container.py` |
| E | `src/gpdatacached/exceptions.py:24-27` | `tests/test_exceptions.py` |
| F | `src/gpdatacached/gc.py:161-163`, `src/gpdatacached/keys.py:116-118` | No new tests |

---

### Task 1: Issue B — Add permission check to `meta.setter`

**Files:**
- Modify: `src/gpdatacached/objects.py:83-88`
- Modify: `tests/test_objects_lifecycle.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_objects_lifecycle.py` after the existing `test_persist_checks_owner` test (after line 119):

```python
def test_meta_setter_checks_owner(domain, ns, clean_redis):
    r = clean_redis
    d = domain.name
    n = ns.name

    parent_name, child_name = _setup_parent_and_child(r, d, n)

    parent_obj = ns.get_object(parent_name)
    child_obj = parent_obj[0]

    from gpdatacached.meta import ObjectMeta

    new_meta = child_obj.meta.model_copy(update={"cache_mode": "ttl", "ttl": 300})

    with pytest.raises(LifecyclePermissionError):
        child_obj.meta = new_meta
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_objects_lifecycle.py::test_meta_setter_checks_owner -v`
Expected: FAIL — `LifecyclePermissionError` is NOT raised (the meta setter bypasses permission check).

- [ ] **Step 3: Write minimal implementation**

In `src/gpdatacached/objects.py`, replace lines 83-88:

```python
    @meta.setter
    def meta(self, new_meta: ObjectMeta) -> None:
        from .ttl import policy_from_meta

        policy = policy_from_meta(new_meta)
        self.set_lifecycle(policy)
```

with:

```python
    @meta.setter
    def meta(self, new_meta: ObjectMeta) -> None:
        from .ttl import policy_from_meta

        ctx = self._load_lifecycle_context()
        self._check_owner_permission(ctx.meta)
        policy = policy_from_meta(new_meta)
        self.set_lifecycle(policy)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_objects_lifecycle.py::test_meta_setter_checks_owner -v`
Expected: PASS

- [ ] **Step 5: Run full lifecycle test suite**

Run: `./venv/bin/python -m pytest tests/test_objects_lifecycle.py tests/test_inherited_lifecycle.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/gpdatacached/objects.py tests/test_objects_lifecycle.py
git commit -m "fix: add owner permission check to meta.setter (issue B)"
```

---

### Task 2: Issue C — Add cross-field validation for `cache_mode` + `ttl` in Config

**Files:**
- Modify: `src/gpdatacached/config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_config.py` after the `TestGPDCNamespaceConfig` class (after line 16):

```python
    def test_rejects_ttl_mode_with_zero_ttl(self):
        import pytest

        with pytest.raises(ValueError, match="default_ttl_seconds > 0"):
            GPDCNamespaceConfig(default_cache_mode="ttl", default_ttl_seconds=0)

    def test_rejects_sliding_mode_with_zero_ttl(self):
        import pytest

        with pytest.raises(ValueError, match="default_ttl_seconds > 0"):
            GPDCNamespaceConfig(default_cache_mode="sliding", default_ttl_seconds=0)

    def test_allows_ttl_mode_none_ttl(self):
        cfg = GPDCNamespaceConfig(default_cache_mode="ttl")
        assert cfg.default_ttl_seconds is None

    def test_allows_permanent_mode_with_zero_ttl(self):
        cfg = GPDCNamespaceConfig(default_cache_mode="permanent", default_ttl_seconds=0)
        assert cfg.default_cache_mode == "permanent"
```

Add to `tests/test_config.py` after the `TestGPDCDomainConfig` class (after line 50):

```python
    def test_rejects_ttl_mode_with_zero_ttl(self):
        import pytest

        with pytest.raises(ValueError, match="default_ttl_seconds > 0"):
            GPDCDomainConfig(default_cache_mode="ttl", default_ttl_seconds=0)

    def test_rejects_sliding_mode_with_zero_ttl(self):
        import pytest

        with pytest.raises(ValueError, match="default_ttl_seconds > 0"):
            GPDCDomainConfig(default_cache_mode="sliding", default_ttl_seconds=0)

    def test_allows_permanent_mode_with_zero_ttl(self):
        cfg = GPDCDomainConfig(default_cache_mode="permanent", default_ttl_seconds=0)
        assert cfg.default_cache_mode == "permanent"
        assert cfg.default_ttl_seconds == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_config.py -v -k "rejects"`
Expected: FAIL — configs accept invalid combinations without error.

- [ ] **Step 3: Write minimal implementation**

In `src/gpdatacached/config.py`, add `model_validator` import to the existing pydantic import on line 6:

Change:
```python
from pydantic import Field, field_validator
```

to:
```python
from pydantic import Field, field_validator, model_validator
```

Add to `GPDCNamespaceConfig` class (after the `_validate_ttl` method, before line 31):

```python
    @model_validator(mode="after")
    def _validate_ttl_mode_consistency(self) -> GPDCNamespaceConfig:
        if (
            self.default_cache_mode in ("ttl", "sliding")
            and self.default_ttl_seconds is not None
            and self.default_ttl_seconds <= 0
        ):
            raise ValueError(
                f"default_cache_mode='{self.default_cache_mode}' requires "
                f"default_ttl_seconds > 0, got {self.default_ttl_seconds}"
            )
        return self
```

Add to `GPDCDomainConfig` class (after the `_validate_ttl` method, before the end of the class at line 62):

```python
    @model_validator(mode="after")
    def _validate_ttl_mode_consistency(self) -> GPDCDomainConfig:
        if self.default_cache_mode in ("ttl", "sliding") and self.default_ttl_seconds <= 0:
            raise ValueError(
                f"default_cache_mode='{self.default_cache_mode}' requires "
                f"default_ttl_seconds > 0, got {self.default_ttl_seconds}"
            )
        return self
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/test_config.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/gpdatacached/config.py tests/test_config.py
git commit -m "fix: add cross-field validation for cache_mode + ttl_seconds in config (issue C)"
```

---

### Task 3: Issue D — Improve slice indexing error message

**Files:**
- Modify: `src/gpdatacached/container.py:202-208`
- Modify: `tests/test_objects_container.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_objects_container.py` in the `__getitem__` section (after the `test_getitem_negative` test, around line 101):

```python
def test_getitem_slice_raises_typeerror(setup_list):
    obj, *_ = setup_list
    with pytest.raises(TypeError, match="slice indexing"):
        obj[1:3]
```

Add in the `__setitem__` section (after the `test_setitem` test, around line 111):

```python
def test_setitem_slice_raises_typeerror(setup_list):
    obj, *_ = setup_list
    with pytest.raises(TypeError, match="slice indexing"):
        obj[1:3] = [99, 100]
```

Add in the `__delitem__` section (after the `test_delitem` test, around line 120):

```python
def test_delitem_slice_raises_typeerror(setup_list):
    obj, *_ = setup_list
    with pytest.raises(TypeError, match="slice indexing"):
        del obj[1:3]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_objects_container.py -v -k "slice"`
Expected: FAIL — raises `TypeError: '<' not supported between instances of 'slice' and 'int'` instead of matching "slice indexing".

- [ ] **Step 3: Write minimal implementation**

In `src/gpdatacached/container.py`, replace the `_normalize_index` method (lines 202-208):

```python
    def _normalize_index(self, index: int, message: str = "list index out of range") -> int:
        length = self._redis().llen(self.raw_key)
        if index < 0:
            index = length + index
        if index < 0 or index >= length:
            raise IndexError(message)
        return index
```

with:

```python
    def _normalize_index(self, index: int, message: str = "list index out of range") -> int:
        if isinstance(index, slice):
            raise TypeError(
                "ListObject does not support slice indexing; "
                "use list(obj)[start:stop] instead"
            )
        length = self._redis().llen(self.raw_key)
        if index < 0:
            index = length + index
        if index < 0 or index >= length:
            raise IndexError(message)
        return index
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/test_objects_container.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/gpdatacached/container.py tests/test_objects_container.py
git commit -m "fix: improve slice indexing error message in ListObject (issue D)"
```

---

### Task 4: Issue E — Include `canonical_uri` in error message

**Files:**
- Modify: `src/gpdatacached/exceptions.py:25-27`
- Modify: `tests/test_exceptions.py`

- [ ] **Step 1: Write the failing test**

Modify `tests/test_exceptions.py` — in `test_message_format` (lines 18-28), add an assertion that the URI is present:

Change:
```python
    def test_message_format(self):
        err = CanonicalObjectReferencedError(
            canonical_name="mylist",
            canonical_uri="gpdc://dom/ns/mylist",
            referrers=["alias1", "parent"],
        )
        msg = str(err)
        assert "mylist" in msg
        assert "alias1" in msg
        assert "parent" in msg
        assert "still referenced by" in msg
```

to:
```python
    def test_message_format(self):
        err = CanonicalObjectReferencedError(
            canonical_name="mylist",
            canonical_uri="gpdc://dom/ns/mylist",
            referrers=["alias1", "parent"],
        )
        msg = str(err)
        assert "mylist" in msg
        assert "gpdc://dom/ns/mylist" in msg
        assert "alias1" in msg
        assert "parent" in msg
        assert "still referenced by" in msg
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_exceptions.py::TestCanonicalObjectReferencedError::test_message_format -v`
Expected: FAIL — the URI is not in the message.

- [ ] **Step 3: Write minimal implementation**

In `src/gpdatacached/exceptions.py`, replace lines 25-27:

```python
        super().__init__(
            f"Cannot delete canonical object {canonical_name!r} because it is still referenced by: [{names}]"
        )
```

with:

```python
        super().__init__(
            f"Cannot delete canonical object {canonical_name!r} ({canonical_uri}) "
            f"because it is still referenced by: [{names}]"
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_exceptions.py -v`
Expected: All PASS

- [ ] **Step 5: Search for other tests that match old message**

Run: `grep -r "Cannot delete canonical object" tests/`
If any tests match the old format, update them too.

- [ ] **Step 6: Commit**

```bash
git add src/gpdatacached/exceptions.py tests/test_exceptions.py
git commit -m "fix: include canonical_uri in CanonicalObjectReferencedError message (issue E)"
```

---

### Task 5: Issue F — Remove dead code for collection:set ref scanning

**Files:**
- Modify: `src/gpdatacached/gc.py:161-163`
- Modify: `src/gpdatacached/keys.py:116-118`
- No new tests

- [ ] **Step 1: Verify existing GC tests pass before change**

Run: `./venv/bin/python -m pytest tests/test_gc.py tests/test_empty_container_gc.py -v`
Expected: All PASS

- [ ] **Step 2: Remove dead code in gc.py**

In `src/gpdatacached/gc.py`, replace lines 161-163:

```python
                    elif member_meta.type == "collection:set":
                        if expected_ref in r.smembers(member_raw_key):
                            still_refs = True
```

with nothing (remove these 3 lines). After the change, lines 160-164 should read:

```python
                        break
                    if not still_refs:
                        r.srem(ref_key, member)
```

- [ ] **Step 3: Remove dead code in keys.py**

In `src/gpdatacached/keys.py`, replace lines 116-118:

```python
    elif raw_type == "collection:set":
        for val in r.smembers(raw_key):
            _extract_ref_name(val, names)
```

with nothing (remove these 3 lines). After the change, lines 115-119 should read:

```python
            _extract_ref_name(val, names)
    return names
```

- [ ] **Step 4: Verify GC tests still pass**

Run: `./venv/bin/python -m pytest tests/test_gc.py tests/test_empty_container_gc.py tests/test_keys.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/gpdatacached/gc.py src/gpdatacached/keys.py
git commit -m "fix: remove unreachable collection:set ref scanning code (issue F)"
```

---

## Post-Implementation Verification

After all 5 tasks are complete:

- [ ] **Run full test suite**

Run: `./venv/bin/python -m pytest -v`
Expected: All PASS

- [ ] **Run linter**

Run: `./venv/bin/python -m ruff check src tests && ./venv/bin/python -m ruff format --check src tests`
Expected: No errors
