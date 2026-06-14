# Refset Extraction + Set→Hash Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract ref set maintenance into `refset.py` with Hash-based reference counting, eliminating the O(n) `_raw_still_contains` scan.

**Architecture:** Two tasks: (1) create `refset.py` with 5 Hash-based functions + unit tests, (2) atomic migration of all src/ callers and test assertions from Set operations to the refset API.

**Tech Stack:** Python 3, Redis, pytest, ruff

**Design doc:** `dev_docs/specs/2026-06-14-refset-extraction-design.md`

---

### Task 1: Create `refset.py` + unit tests

**Files:**
- Create: `src/gpdatacached/refset.py`
- Create: `tests/test_refset.py`

- [ ] **Step 1: Run baseline tests**

Run: `./venv/bin/python -m pytest -x -q`
Expected: 928 passed

- [ ] **Step 2: Create `src/gpdatacached/refset.py`**

```python
"""Reference set maintenance for GPDC container objects.

Ref sets track which objects reference a given container's raw data.
Uses Redis Hash to track reference counts — each field is a referrer
name, each value is the number of slots referencing the target.
"""

from __future__ import annotations


def add_referrer(r, ref_key: str, referrer_name: str) -> None:
    """Register a reference from referrer_name (increment count)."""
    r.hincrby(ref_key, referrer_name, 1)


def release_ref(r, ref_key: str, referrer_name: str) -> bool:
    """Release one reference (decrement count).

    Returns True if the referrer was fully removed (count reached zero).
    """
    remaining = r.hincrby(ref_key, referrer_name, -1)
    if remaining <= 0:
        r.hdel(ref_key, referrer_name)
        return True
    return False


def remove_referrer(r, ref_key: str, referrer_name: str) -> None:
    """Remove referrer entirely regardless of count (for teardown/GC)."""
    r.hdel(ref_key, referrer_name)


def get_referrers(r, ref_key: str) -> set[str]:
    """Return the set of referrer names."""
    return set(r.hkeys(ref_key))


def has_referrers(r, ref_key: str) -> bool:
    """Return True if any referrers exist."""
    return r.hlen(ref_key) > 0
```

- [ ] **Step 3: Create `tests/test_refset.py`**

```python
"""Unit tests for refset module."""

import pytest

from gpdatacached.refset import (
    add_referrer,
    release_ref,
    remove_referrer,
    get_referrers,
    has_referrers,
)


@pytest.fixture
def r(clean_redis):
    return clean_redis


@pytest.fixture
def ref_key():
    return "gpdc:testdomain:testns:ref:testobj"


class TestAddReferrer:
    def test_add_first_referrer(self, r, ref_key):
        add_referrer(r, ref_key, "parent")
        assert r.hget(ref_key, "parent") == "1"

    def test_add_same_referrer_twice_increments_count(self, r, ref_key):
        add_referrer(r, ref_key, "parent")
        add_referrer(r, ref_key, "parent")
        assert r.hget(ref_key, "parent") == "2"

    def test_add_multiple_referrers(self, r, ref_key):
        add_referrer(r, ref_key, "parent")
        add_referrer(r, ref_key, "alias")
        assert r.hget(ref_key, "parent") == "1"
        assert r.hget(ref_key, "alias") == "1"


class TestReleaseRef:
    def test_release_decrements_count(self, r, ref_key):
        add_referrer(r, ref_key, "parent")
        add_referrer(r, ref_key, "parent")
        fully_released = release_ref(r, ref_key, "parent")
        assert not fully_released
        assert r.hget(ref_key, "parent") == "1"

    def test_release_removes_entry_at_zero(self, r, ref_key):
        add_referrer(r, ref_key, "parent")
        fully_released = release_ref(r, ref_key, "parent")
        assert fully_released
        assert r.hexists(ref_key, "parent") is False

    def test_release_on_nonexistent_returns_true(self, r, ref_key):
        fully_released = release_ref(r, ref_key, "ghost")
        assert fully_released


class TestRemoveReferrer:
    def test_remove_entirely(self, r, ref_key):
        add_referrer(r, ref_key, "parent")
        add_referrer(r, ref_key, "parent")
        remove_referrer(r, ref_key, "parent")
        assert r.hexists(ref_key, "parent") is False

    def test_remove_nonexistent_is_noop(self, r, ref_key):
        remove_referrer(r, ref_key, "ghost")
        assert r.hlen(ref_key) == 0


class TestGetReferrers:
    def test_empty_ref_set(self, r, ref_key):
        assert get_referrers(r, ref_key) == set()

    def test_returns_all_referrer_names(self, r, ref_key):
        add_referrer(r, ref_key, "parent")
        add_referrer(r, ref_key, "alias")
        result = get_referrers(r, ref_key)
        assert result == {"parent", "alias"}


class TestHasReferrers:
    def test_empty_returns_false(self, r, ref_key):
        assert has_referrers(r, ref_key) is False

    def test_with_referrers_returns_true(self, r, ref_key):
        add_referrer(r, ref_key, "parent")
        assert has_referrers(r, ref_key) is True

    def test_after_full_release_returns_false(self, r, ref_key):
        add_referrer(r, ref_key, "parent")
        release_ref(r, ref_key, "parent")
        assert has_referrers(r, ref_key) is False
```

- [ ] **Step 4: Run lint and refset tests**

Run: `./venv/bin/ruff check src/gpdatacached/refset.py tests/test_refset.py`
Expected: All checks passed

Run: `./venv/bin/python -m pytest tests/test_refset.py -v`
Expected: All tests pass

- [ ] **Step 5: Verify existing tests still pass**

Run: `./venv/bin/python -m pytest -x -q`
Expected: 928 passed (refset.py is standalone, not yet imported by any src/ file)

- [ ] **Step 6: Commit**

```bash
git add src/gpdatacached/refset.py tests/test_refset.py
git commit -m "feat: add refset module with Hash-based reference counting

Five functions encapsulating all ref set content operations:
- add_referrer: HINCRBY +1
- release_ref: HINCRBY -1, HDEL at zero, returns bool
- remove_referrer: HDEL (hard remove for teardown)
- get_referrers: HKEYS
- has_referrers: HLEN > 0"
```

---

### Task 2: Atomic migration — update all src/ callers and test assertions

This is a large but mechanical task. ALL changes must be made before running the test suite, because the data structure changes from Set to Hash.

**Files:**
- Modify: `src/gpdatacached/container.py`
- Modify: `src/gpdatacached/namespace.py`
- Modify: `src/gpdatacached/types.py`
- Modify: `src/gpdatacached/gc.py`
- Modify: `tests/test_namespace.py`
- Modify: `tests/test_container.py`
- Modify: `tests/test_gc.py`
- Modify: `tests/test_nested_containers.py`
- Modify: `tests/test_objects.py`

- [ ] **Step 1: Migrate `container.py`**

**1a. Add import at top of file:**

In `src/gpdatacached/container.py`, add to the imports section (after existing imports):

```python
from .refset import release_ref, get_referrers, has_referrers
```

**1b. Delete `_raw_still_contains` method (lines ~140-150):**

Remove the entire method:

```python
    def _raw_still_contains(self, encoded: str) -> bool:
        """Check whether ``encoded`` value still exists in this container's raw data."""
        if not self.raw_key:
            return False
        r = self._redis()
        redis_type = r.type(self.raw_key)
        if redis_type == "list":
            return encoded in r.lrange(self.raw_key, 0, -1)
        if redis_type == "hash":
            return encoded in r.hvals(self.raw_key)
        return False
```

**1c. Delete `_remove_self_from_refset` method (lines ~152-155):**

Remove the entire method:

```python
    def _remove_self_from_refset(self, target_name: str) -> None:
        """Remove this container's data-owner name from the target object's ref set."""
        target_ref_key = make_ref_key(self._namespace.domain.name, self._namespace.name, target_name)
        self._redis().srem(target_ref_key, self._data_owner_name())
```

**1d. Simplify `_release_element_ref` method:**

Replace the entire method body with:

```python
    def _release_element_ref(self, old_encoded: str | None) -> None:
        """Release the ref binding held by a removed or overwritten element.

        Decrements the reference count in the target's ref hash. If the
        count reaches zero, cascade-deletes the target if it is an
        orphaned anonymous object.
        """
        if not isinstance(old_encoded, str) or not old_encoded.startswith("ref:"):
            return
        target_name = parse_ref_encoded(old_encoded)
        if target_name is None:
            return
        target_ref_key = make_ref_key(self._namespace.domain.name, self._namespace.name, target_name)
        fully_released = release_ref(self._redis(), target_ref_key, self._data_owner_name())
        if fully_released:
            self._gc_anonymous_if_orphaned(target_name)
```

**1e. Update `_gc_anonymous_if_orphaned` — replace `r.smembers` with `has_referrers`:**

Change the line:
```python
        if r.smembers(target_ref_key):
            return
```
to:
```python
        if has_referrers(r, target_ref_key):
            return
```

**1f. Update `referrers()` — replace `smembers` with `get_referrers`:**

Change:
```python
        return self._redis().smembers(ref_key)
```
to:
```python
        return get_referrers(self._redis(), ref_key)
```

**1g. Update class docstring** — change "Redis set" to "Redis hash":

Replace:
```
    a Redis set (``ref_key``). Subclasses implement
```
with:
```
    a Redis hash (``ref_key``). Subclasses implement
```

Replace:
```
        ref_key: Redis set key recording the names of objects that own or
```
with:
```
        ref_key: Redis hash key recording referrer names and their reference
            counts; used for ref-counting and cascade-deletion of anonymous
```

- [ ] **Step 2: Migrate `namespace.py`**

**2a. Add import:**

In `src/gpdatacached/namespace.py`, add after existing imports:

```python
from .refset import add_referrer, release_ref, remove_referrer, get_referrers, has_referrers
```

**2b. In `_cleanup_alias_binding` (~line 179-187):**

Replace `r.srem(ref_key, key)` with `release_ref(r, ref_key, key)`.

Replace `if canonical_name.startswith("~") and not r.smembers(ref_key):` with `if canonical_name.startswith("~") and not has_referrers(r, ref_key):`.

**2c. In `_cleanup_existing_for_overwrite` (~line 189-208):**

Replace `referrers = r.smembers(ref_key)` with `referrers = get_referrers(r, ref_key)`.

**2d. In `_teardown_owner_container` (~line 210-227):**

Replace `r.srem(nested_ref_key, key)` with `remove_referrer(r, nested_ref_key, key)`.

**2e. In `_setitem_container_ref` (~line 280-295):**

Replace `r.sadd(ref_key, key)` with `add_referrer(r, ref_key, key)`.

**2f. In `__setitem__` (~line 297-377):**

Replace `referrers = r.smembers(ref_key)` with `referrers = get_referrers(r, ref_key)`.

**2g. In `__delitem__` (~line 425-461):**

In the reference branch, replace `r.srem(ref_key, key)` with `release_ref(r, ref_key, key)`.
Replace `if canonical_name.startswith("~") and not r.smembers(ref_key):` with `if canonical_name.startswith("~") and not has_referrers(r, ref_key):`.

In the owner branch, replace `referrers = r.smembers(ref_key)` with `referrers = get_referrers(r, ref_key)`.

**2h. In `_cascade_delete_anonymous` (~line 463-488):**

Replace `referrers = r.smembers(ref_key)` with `referrers = get_referrers(r, ref_key)`.

Replace `r.srem(nested_ref_key, anon_name)` with `remove_referrer(r, nested_ref_key, anon_name)`.

- [ ] **Step 3: Migrate `types.py`**

**3a. Add import:**

In `src/gpdatacached/types.py`, add after existing imports:

```python
from .refset import add_referrer
```

**3b. In `_ref_existing_container` (~line 122-136):**

Replace `namespace._redis().sadd(ref_key, parent_name)` with `add_referrer(namespace._redis(), ref_key, parent_name)`.

**3c. In `_create_anonymous_object` (~line 159-202):**

Replace `r.sadd(ref_key, parent_name)` with `add_referrer(r, ref_key, parent_name)`.

- [ ] **Step 4: Migrate `gc.py`**

**4a. Add import:**

In `src/gpdatacached/gc.py`, add after existing imports:

```python
from .refset import remove_referrer, get_referrers
```

**4b. In `_gc_scan_raw_keys` (~line 73-106):**

Replace `referrers = r.smembers(ref_key)` with `referrers = get_referrers(r, ref_key)`.

There are two occurrences in this function (one for `if not referrers:` and one for the `for ref_name in referrers:` loop). Update both by replacing `r.smembers(ref_key)` with `get_referrers(r, ref_key)`.

**4c. In `_gc_scan_ref_keys` (~line 109-168):**

Replace `members = r.smembers(ref_key)` with `members = get_referrers(r, ref_key)`.

Replace ALL 6 occurrences of `r.srem(ref_key, member)` with `remove_referrer(r, ref_key, member)`.

- [ ] **Step 5: Migrate `tests/test_namespace.py`**

Apply these transformations ONLY where the variable is a ref_key (constructed via `make_ref_key`). Do NOT change `smembers` calls on `make_domain_namespaces_key`, `make_raw_key`, or other non-ref keys.

**5a.** For membership checks like `assert "x" in clean_redis.smembers(ref_key)`:
→ Change to `assert clean_redis.hexists(ref_key, "x")`

**5b.** For negative membership like `assert "x" not in clean_redis.smembers(ref_key)`:
→ Change to `assert not clean_redis.hexists(ref_key, "x")`

**5c.** For equality checks like `assert r.smembers(ref_key) == {"alias"}`:
→ Change to `assert set(r.hkeys(ref_key)) == {"alias"}`

**5d.** For empty checks like `assert r.smembers(ref_key) == set()`:
→ Change to `assert r.hlen(ref_key) == 0`

Lines to update (approximate): 704, 739, 756, 758, 796, 873, 983, 997, 1034, 1177, 1228, 1474, 1477, 1483, 1486, 1495, 1496, 1506, 1507, 1516, 1550, 1559, 1568.

Do NOT change line 20 (`make_domain_namespaces_key`) or line 921 (`raw_key`).

- [ ] **Step 6: Migrate `tests/test_container.py`**

Line 305: Replace `r.sadd(ref_key, "other_obj")` with `r.hset(ref_key, "other_obj", "1")`.

- [ ] **Step 7: Migrate `tests/test_gc.py`**

For `sadd` on ref keys, replace with `hset`:
- Line 35: `clean_redis.sadd(ref_x, "orphan_referrer")` → `clean_redis.hset(ref_x, "orphan_referrer", "1")`
- Line 191: `clean_redis.sadd(ref_key, "scalar")` → `clean_redis.hset(ref_key, "scalar", "1")`
- Lines 388, 389: `clean_redis.sadd(...)` → `clean_redis.hset(..., "1")`

For `smembers` on ref keys, apply the same transformation rules as Step 5 (5a for membership, 5c for equality).

Lines to update: 35, 141, 154, 158, 168, 174, 182, 185, 191, 192, 195, 388, 389, 391, 394.

- [ ] **Step 8: Migrate `tests/test_nested_containers.py`**

Lines 78, 576: `assert "mydict" in r.smembers(ref_key)` → `assert r.hexists(ref_key, "mydict")`

- [ ] **Step 9: Migrate `tests/test_objects.py`**

Line 150: `r.sadd(make_ref_key(domain, ns, child_name), parent_name)` → `r.hset(make_ref_key(domain, ns, child_name), parent_name, "1")`

Line 197: `r.sadd(make_ref_key(d, n, child_name), ref_name)` → `r.hset(make_ref_key(d, n, child_name), ref_name, "1")`

- [ ] **Step 10: Run lint**

Run: `./venv/bin/ruff check src tests`
Expected: All checks passed

- [ ] **Step 11: Run full test suite**

Run: `./venv/bin/python -m pytest -x -q`
Expected: 928 passed + ~15 new refset tests = ~943 passed

If tests fail, the most likely causes are:
- Missed a `smembers`/`sadd`/`srem` on a ref key somewhere — search for remaining occurrences
- A `smembers` on a non-ref key was accidentally changed — revert it
- A ref_key variable was confused with a non-ref key — check the variable construction

- [ ] **Step 12: Commit**

```bash
git add src/gpdatacached/container.py src/gpdatacached/namespace.py src/gpdatacached/types.py src/gpdatacached/gc.py tests/test_namespace.py tests/test_container.py tests/test_gc.py tests/test_nested_containers.py tests/test_objects.py
git commit -m "refactor: migrate ref sets from Redis Set to Hash with reference counting

- All src/ callers now use refset.py API (add_referrer, release_ref,
  remove_referrer, get_referrers, has_referrers)
- _raw_still_contains and _remove_self_from_refset deleted from
  container.py — count-based release_ref replaces O(n) raw data scan
- All test assertions updated from Set to Hash operations
- Slot-level ref removal goes from O(n) to O(1)"
```
