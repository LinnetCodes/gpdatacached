# P0-3: Stale Alias Mapping Semantics Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make stale aliases invisible across all public mapping methods (`__contains__`, `__iter__`, `__len__`, `__getitem__`) by using `_resolve_binding()` as the single source of truth.

**Architecture:** Modify `__contains__` and `__iter__` in `namespace.py` to filter through `_resolve_binding()`. `__len__` auto-corrects since it delegates to `__iter__`. `__getitem__` already works correctly. Add comprehensive stale alias tests.

**Tech Stack:** Python, Redis, pytest

---

### Task 1: Write failing tests for stale alias mapping semantics

**Files:**
- Create: `tests/test_stale_alias_semantics.py`

- [ ] **Step 1: Write all failing tests**

```python
"""Tests for P0-3: stale alias semantics in public mapping interface.

When a canonical owner expires or is deleted, the alias binding (Redis string)
remains until GC runs. These tests verify that all public mapping methods
treat stale aliases as non-existent, regardless of GC state.
"""

import pytest
from gpdatacached.keys import make_object_key, make_ref_key


class TestStaleAliasContains:
    """__contains__ returns False for stale aliases."""

    def test_stale_alias_not_in_ns(self, ns):
        ns["owner"] = [1, 2, 3]
        ns["alias"] = ns["owner"]
        r = ns._redis()
        owner_key = make_object_key(ns.domain.name, ns.name, "owner")
        ref_key = make_ref_key(ns.domain.name, ns.name, "owner")
        r.delete(owner_key)
        r.delete(ref_key)
        assert "alias" not in ns

    def test_valid_alias_in_ns(self, ns):
        ns["owner"] = [1, 2, 3]
        ns["alias"] = ns["owner"]
        assert "alias" in ns

    def test_owner_still_in_ns_after_owner_deleted(self, ns):
        ns["owner"] = [1, 2, 3]
        assert "owner" in ns
        del ns["owner"]
        assert "owner" not in ns

    def test_stale_alias_string_key_still_exists_in_redis(self, ns):
        ns["owner"] = [1, 2, 3]
        ns["alias"] = ns["owner"]
        r = ns._redis()
        owner_key = make_object_key(ns.domain.name, ns.name, "owner")
        ref_key = make_ref_key(ns.domain.name, ns.name, "owner")
        r.delete(owner_key)
        r.delete(ref_key)
        alias_key = make_object_key(ns.domain.name, ns.name, "alias")
        assert r.exists(alias_key) > 0
        assert "alias" not in ns


class TestStaleAliasGetItem:
    """__getitem__ raises KeyError for stale aliases."""

    def test_stale_alias_getitem_raises_keyerror(self, ns):
        ns["owner"] = [1, 2, 3]
        ns["alias"] = ns["owner"]
        r = ns._redis()
        owner_key = make_object_key(ns.domain.name, ns.name, "owner")
        ref_key = make_ref_key(ns.domain.name, ns.name, "owner")
        r.delete(owner_key)
        r.delete(ref_key)
        with pytest.raises(KeyError):
            ns["alias"]

    def test_valid_alias_getitem_works(self, ns):
        ns["owner"] = [1, 2, 3]
        ns["alias"] = ns["owner"]
        assert ns["alias"].value == [1, 2, 3]


class TestStaleAliasIter:
    """__iter__ does not yield stale alias names."""

    def test_iter_excludes_stale_alias(self, ns):
        ns["owner"] = [1, 2, 3]
        ns["alias"] = ns["owner"]
        ns["other"] = "hello"
        r = ns._redis()
        owner_key = make_object_key(ns.domain.name, ns.name, "owner")
        ref_key = make_ref_key(ns.domain.name, ns.name, "owner")
        r.delete(owner_key)
        r.delete(ref_key)
        names = list(ns)
        assert "alias" not in names
        assert "other" in names

    def test_iter_includes_valid_alias(self, ns):
        ns["owner"] = [1, 2, 3]
        ns["alias"] = ns["owner"]
        names = list(ns)
        assert "alias" in names
        assert "owner" in names

    def test_iter_multiple_stale_and_valid(self, ns):
        ns["owner1"] = [1, 2]
        ns["owner2"] = [3, 4]
        ns["alias1"] = ns["owner1"]
        ns["alias2"] = ns["owner2"]
        ns["standalone"] = "val"
        r = ns._redis()
        owner1_key = make_object_key(ns.domain.name, ns.name, "owner1")
        ref1_key = make_ref_key(ns.domain.name, ns.name, "owner1")
        r.delete(owner1_key)
        r.delete(ref1_key)
        names = list(ns)
        assert "alias1" not in names
        assert "owner1" not in names
        assert "alias2" in names
        assert "owner2" in names
        assert "standalone" in names


class TestStaleAliasLen:
    """__len__ does not count stale aliases."""

    def test_len_excludes_stale_alias(self, ns):
        ns["owner"] = [1, 2, 3]
        ns["alias"] = ns["owner"]
        ns["other"] = "hello"
        r = ns._redis()
        owner_key = make_object_key(ns.domain.name, ns.name, "owner")
        ref_key = make_ref_key(ns.domain.name, ns.name, "owner")
        r.delete(owner_key)
        r.delete(ref_key)
        assert len(ns) == 2
        assert set(ns) == {"alias", "other"}

    def test_stale_alias_not_counted_fix(self, ns):
        ns["owner"] = [1, 2, 3]
        ns["alias"] = ns["owner"]
        r = ns._redis()
        owner_key = make_object_key(ns.domain.name, ns.name, "owner")
        ref_key = make_ref_key(ns.domain.name, ns.name, "owner")
        r.delete(owner_key)
        r.delete(ref_key)
        assert len(ns) == 0

    def test_len_counts_valid_alias(self, ns):
        ns["owner"] = [1, 2, 3]
        ns["alias"] = ns["owner"]
        assert len(ns) == 2


class TestStaleAliasScalarOwner:
    """Stale alias to scalar owner also handled correctly."""

    def test_stale_alias_to_scalar_not_in_ns(self, ns):
        ns["num"] = 42
        ns["alias"] = ns.get_object("num")
        r = ns._redis()
        num_key = make_object_key(ns.domain.name, ns.name, "num")
        r.delete(num_key)
        assert "alias" not in ns

    def test_stale_alias_to_scalar_iter_excluded(self, ns):
        ns["num"] = 42
        ns["alias"] = ns.get_object("num")
        ns["other"] = "hello"
        r = ns._redis()
        num_key = make_object_key(ns.domain.name, ns.name, "num")
        r.delete(num_key)
        names = list(ns)
        assert "alias" not in names
        assert "other" in names
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_stale_alias_semantics.py -v`
Expected: Multiple FAIL — `__contains__` returns True for stale aliases, `__iter__` yields them, `__len__` counts them.

- [ ] **Step 3: Commit failing tests**

```bash
git add tests/test_stale_alias_semantics.py
git commit -m "test: add stale alias mapping semantics tests (P0-3)"
```

---

### Task 2: Fix `__contains__` to use `_resolve_binding()`

**Files:**
- Modify: `src/gpdatacached/namespace.py:473-479`

- [ ] **Step 1: Update `__contains__` implementation**

Replace the current `__contains__` method (lines 473-479):

```python
    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        if key.startswith("~"):
            return False
        return self._resolve_binding(key) is not None
```

- [ ] **Step 2: Run stale alias contains tests**

Run: `./venv/bin/python -m pytest tests/test_stale_alias_semantics.py::TestStaleAliasContains tests/test_stale_alias_semantics.py::TestStaleAliasScalarOwner::test_stale_alias_to_scalar_not_in_ns -v`
Expected: PASS

- [ ] **Step 3: Run existing tests to verify no regression**

Run: `./venv/bin/python -m pytest tests/test_alias_binding.py tests/test_namespace.py -v`
Expected: ALL PASS

---

### Task 3: Fix `__iter__` to filter through `_resolve_binding()`

**Files:**
- Modify: `src/gpdatacached/namespace.py:481-496`

- [ ] **Step 1: Update `__iter__` implementation**

Replace the current `__iter__` method (lines 481-496):

```python
    def __iter__(self) -> Iterator[str]:
        """Iterate over named object names using SCAN (no KEYS).

        Anonymous objects (names starting with ``~``) are excluded.
        Stale aliases whose canonical owner no longer exists are excluded.
        """
        pattern = make_object_key(self._domain.name, self._name, "*")
        prefix = make_object_key_prefix(self._domain.name, self._name)
        cursor = 0
        while True:
            cursor, keys = self._redis().scan(cursor=cursor, match=pattern, count=100)
            for k in keys:
                name = k[len(prefix) :]
                if not name.startswith("~"):
                    if self._resolve_binding(name) is not None:
                        yield name
            if cursor == 0:
                break
```

- [ ] **Step 2: Run stale alias iter and len tests**

Run: `./venv/bin/python -m pytest tests/test_stale_alias_semantics.py -v`
Expected: ALL PASS

- [ ] **Step 3: Run full test suite**

Run: `./venv/bin/python -m pytest -v`
Expected: ALL PASS

- [ ] **Step 4: Commit implementation**

```bash
git add src/gpdatacached/namespace.py
git commit -m "fix: stale aliases invisible in __contains__, __iter__, __len__ (P0-3)"
```

---

### Task 4: Run lint and full verification

- [ ] **Step 1: Run ruff check**

Run: `./venv/bin/python -m ruff check src tests`
Expected: No errors

- [ ] **Step 2: Run ruff format check**

Run: `./venv/bin/python -m ruff format --check src tests`
Expected: No changes needed

- [ ] **Step 3: Run full test suite one final time**

Run: `./venv/bin/python -m pytest -v`
Expected: ALL PASS (735+ tests)
