# Lifecycle Management Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor `ttl.py` → `lifecycle.py`: fix the broken sliding-TTL refresh, centralize validation, collapse TTL-application helpers, clean propagation, and relocate `_LifecycleContext`.

**Architecture:** Module rename first (mechanical, keeps API identical), then incremental behavior changes each leaving the suite green: collapse `apply_policy`/`apply_lifecycle` → `apply_ttl`; add `propagate_ttl_refresh` + `refresh_ttl` to fix sliding refresh; convert `expire_seconds` to a derived property with two-tier validation; drop `visited` from `propagate_lifecycle`; move `_LifecycleContext` to `objects.py`.

**Tech Stack:** Python 3, pydantic, redis-py, pytest, ruff. Test Redis: `127.0.0.1:6379` db=1, password `123456`.

**Spec:** `dev_docs/specs/2026-06-16-lifecycle-management-refactor-design.md`
**Working branch:** `lifecycle-managment-refactor` (current branch — no worktree, per user instruction)

**Run all tests:** `./venv/bin/python -m pytest`
**Lint/format:** `./venv/bin/python -m ruff check src tests` and `./venv/bin/python -m ruff format src tests`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/gpdatacached/lifecycle.py` | rename from `ttl.py` + edit | `TTLPolicy`, validation, `apply_ttl`, `propagate_lifecycle`, `propagate_ttl_refresh` |
| `src/gpdatacached/objects.py` | edit | `_LifecycleContext` (moved in), `set_lifecycle`, `refresh_ttl`, `update_cache_mode_and_ttl`, `expire_in/at`, `persist` |
| `src/gpdatacached/container.py` | edit | `_touch`, `_propagate_sliding_ttl_upward` rewired to `refresh_ttl` |
| `src/gpdatacached/scalar.py` | edit | value setter rewired to `refresh_ttl` |
| `src/gpdatacached/types.py` | edit | `_create_anonymous_object` uses `apply_ttl` |
| `src/gpdatacached/namespace.py` | edit | `apply_lifecycle` → `apply_ttl` |
| `src/gpdatacached/builtins/list_object.py`, `dict_object.py` | edit | import path only |
| `src/gpdatacached/extensions/pydantic_support.py` | edit | import path only |
| `src/gpdatacached/config.py` | edit | delegate validation to `validate_cache_mode_and_ttl` |
| `tests/test_lifecycle.py` | rename from `test_ttl.py` + edit | updated + strengthened tests |
| `tests/test_meta.py`, `tests/test_objects.py` | edit | import path + `TTLPolicy` construction |

---

## Task 1: Mechanical rename `ttl.py` → `lifecycle.py`

**Files:**
- Rename: `src/gpdatacached/ttl.py` → `src/gpdatacached/lifecycle.py`
- Rename: `tests/test_ttl.py` → `tests/test_lifecycle.py`
- Edit import paths (NO logic change): `src/gpdatacached/{objects,container,scalar,types,namespace}.py`, `src/gpdatacached/builtins/{list_object,dict_object}.py`, `src/gpdatacached/extensions/pydantic_support.py`, `tests/{test_lifecycle,test_meta,test_objects}.py`

This task changes only module paths; every public name keeps its current signature, so the suite stays green.

- [ ] **Step 1: Rename the two files with git**

```bash
git mv src/gpdatacached/ttl.py src/gpdatacached/lifecycle.py
git mv tests/test_ttl.py tests/test_lifecycle.py
```

- [ ] **Step 2: Replace the module header comment with a docstring**

In `src/gpdatacached/lifecycle.py`, replace the first line `# src/gpdatacached/ttl.py` with:

```python
"""Data-object lifecycle management: cache-mode/TTL policy, Redis TTL application, and propagation."""
```

- [ ] **Step 3: Update all import sites**

Apply these exact replacements (the rule is: `.ttl import` → `.lifecycle import`, `..ttl import` → `..lifecycle import`, `gpdatacached.ttl` → `gpdatacached.lifecycle`):

| File | Old | New |
|------|-----|-----|
| `src/gpdatacached/scalar.py:39` | `from .ttl import policy_from_meta, policy_for_sliding_touch` | `from .lifecycle import policy_from_meta, policy_for_sliding_touch` |
| `src/gpdatacached/container.py:52` | `from .ttl import policy_from_meta, policy_for_sliding_touch` | `from .lifecycle import policy_from_meta, policy_for_sliding_touch` |
| `src/gpdatacached/container.py:79` | `from .ttl import policy_from_meta, policy_for_sliding_touch` | `from .lifecycle import policy_from_meta, policy_for_sliding_touch` |
| `src/gpdatacached/types.py:166` | `from .ttl import apply_lifecycle` | `from .lifecycle import apply_lifecycle` |
| `src/gpdatacached/objects.py:14` | `from .ttl import _LifecycleContext` | `from .lifecycle import _LifecycleContext` |
| `src/gpdatacached/objects.py:111-115` | `from .ttl import (\n _validate_policy,\n policy_from_meta,\n apply_lifecycle,\n propagate_lifecycle,\n)` | same names, `from .lifecycle import` |
| `src/gpdatacached/objects.py:145,198,208,222` | `from .ttl import TTLPolicy` | `from .lifecycle import TTLPolicy` |
| `src/gpdatacached/namespace.py:238,368` | `from .ttl import apply_lifecycle` | `from .lifecycle import apply_lifecycle` |
| `src/gpdatacached/namespace.py:342` | `from .ttl import policy_from_meta` | `from .lifecycle import policy_from_meta` |
| `src/gpdatacached/namespace.py:347` | `from .ttl import policy_from_defaults` | `from .lifecycle import policy_from_defaults` |
| `src/gpdatacached/builtins/list_object.py:9` | `from ..ttl import policy_from_meta` | `from ..lifecycle import policy_from_meta` |
| `src/gpdatacached/builtins/list_object.py:18` | `from ..ttl import policy_from_defaults` | `from ..lifecycle import policy_from_defaults` |
| `src/gpdatacached/builtins/dict_object.py:8` | `from ..ttl import policy_from_meta` | `from ..lifecycle import policy_from_meta` |
| `src/gpdatacached/builtins/dict_object.py:17` | `from ..ttl import policy_from_defaults` | `from ..lifecycle import policy_from_defaults` |
| `src/gpdatacached/extensions/pydantic_support.py:66` | `from ..ttl import policy_from_defaults` | `from ..lifecycle import policy_from_defaults` |
| `src/gpdatacached/extensions/pydantic_support.py:137` | `from ..ttl import policy_from_meta` | `from ..lifecycle import policy_from_meta` |
| `tests/test_lifecycle.py:10` | `from gpdatacached.ttl import (` | `from gpdatacached.lifecycle import (` |
| `tests/test_lifecycle.py:150` | `import gpdatacached.ttl as ttl_mod` | `import gpdatacached.lifecycle as ttl_mod` |
| `tests/test_lifecycle.py:156,163,170,177` | `from gpdatacached.ttl import _validate_policy, TTLPolicy` | `from gpdatacached.lifecycle import _validate_policy, TTLPolicy` |
| `tests/test_lifecycle.py:184` | `from gpdatacached.ttl import propagate_lifecycle, TTLPolicy` | `from gpdatacached.lifecycle import propagate_lifecycle, TTLPolicy` |
| `tests/test_meta.py:10` | `from gpdatacached.ttl import TTLPolicy, apply_lifecycle` | `from gpdatacached.lifecycle import TTLPolicy, apply_lifecycle` |
| `tests/test_objects.py:9` | `from gpdatacached.ttl import TTLPolicy` | `from gpdatacached.lifecycle import TTLPolicy` |

- [ ] **Step 4: Verify no stale references remain**

Run: `rg -n "gpdatacached\.ttl|from \.ttl import|from \.\.ttl import|import ttl" src tests`
Expected: **no output** (empty).

- [ ] **Step 5: Run the full suite**

Run: `./venv/bin/python -m pytest -q`
Expected: all tests PASS (same count as before rename; `test_ttl.py` was 42 passing).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor: rename ttl.py to lifecycle.py"
```

---

## Task 2: Collapse `apply_policy` / `apply_lifecycle` → `apply_ttl` (F5)

**Files:**
- Modify: `src/gpdatacached/lifecycle.py` (replace `apply_policy` + `apply_lifecycle` with `apply_ttl`)
- Modify: `src/gpdatacached/objects.py` (`set_lifecycle` call site)
- Modify: `src/gpdatacached/namespace.py` (two call sites)
- Modify: `src/gpdatacached/types.py` (`_create_anonymous_object`)
- Modify: `tests/test_lifecycle.py`, `tests/test_meta.py` (test call sites)

`expire_seconds` is still a constructor field in this task; only the TTL-application helpers change shape.

- [ ] **Step 1: Update the unit tests first (they will fail to import the old names)**

In `tests/test_lifecycle.py`, the `TestApplyPolicy` class uses `apply_policy`. Rename the class to `TestApplyTtl` and replace `apply_policy(r, "testkey", p)` with `apply_ttl(r, ["testkey"], p)`, and the import `apply_policy` → `apply_ttl`:

```python
class TestApplyTtl:
    def test_persist_called_when_expire_seconds_is_none(self):
        r = MagicMock()
        p = TTLPolicy(cache_mode="permanent", declared_ttl=0, expire_seconds=None)
        apply_ttl(r, ["testkey"], p)
        r.persist.assert_called_once_with("testkey")
        r.expire.assert_not_called()

    def test_expire_called_when_expire_seconds_set(self):
        r = MagicMock()
        p = TTLPolicy(cache_mode="ttl", declared_ttl=300, expire_seconds=300)
        apply_ttl(r, ["testkey"], p)
        r.expire.assert_called_once_with("testkey", 300)
        r.persist.assert_not_called()
```

Update the top-of-file import list: replace `apply_policy,` with `apply_ttl,`.

In `tests/test_meta.py:264,270,278,286`, replace each `apply_lifecycle(r, "gpdc:d:ns:obj:x", "gpdc:d:ns:raw:x", "gpdc:d:ns:ref:x", True, policy)` with `apply_ttl(r, ["gpdc:d:ns:obj:x", "gpdc:d:ns:raw:x", "gpdc:d:ns:ref:x"], policy)`, and the `False` case `apply_lifecycle(r, "gpdc:d:ns:obj:x", "", "", False, policy)` with `apply_ttl(r, ["gpdc:d:ns:obj:x"], policy)`. Update the import `from gpdatacached.lifecycle import TTLPolicy, apply_lifecycle` → `from gpdatacached.lifecycle import TTLPolicy, apply_ttl`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_lifecycle.py::TestApplyTtl tests/test_meta.py -q`
Expected: FAIL (`ImportError: cannot import name 'apply_ttl'`).

- [ ] **Step 3: Implement `apply_ttl` and remove the old helpers**

In `src/gpdatacached/lifecycle.py`, replace the two functions `apply_policy` (lines ~67-71) and `apply_lifecycle` (lines ~74-80) with a single function:

```python
def apply_ttl(r, keys, policy: TTLPolicy) -> None:
    """Apply the policy's TTL to each non-empty key: PERSIST when no expiry, EXPIRE otherwise."""
    seconds = policy.expire_seconds
    for k in keys:
        if not k:
            continue
        if seconds is None:
            r.persist(k)
        else:
            r.expire(k, seconds)
```

- [ ] **Step 4: Update `objects.set_lifecycle` call site**

In `src/gpdatacached/objects.py`, inside `set_lifecycle`, replace:

```python
            apply_lifecycle(ctx.r, ctx.key, ctx.raw_key, ctx.ref_key, ctx.is_collection, policy)
```

with:

```python
            apply_ttl(ctx.r, [ctx.key, ctx.raw_key, ctx.ref_key], policy)
```

and update the local import block to import `apply_ttl` instead of `apply_lifecycle`:

```python
        from .lifecycle import (
            policy_from_meta,
            apply_ttl,
            propagate_lifecycle,
        )
```

(Keep `_validate_policy` in the import only if still used elsewhere — it is still used on the next line in this task. Actually `set_lifecycle` currently calls `_validate_policy(policy)`; leave that import + call intact for now; it is removed in Task 4. So the import becomes `from .lifecycle import (_validate_policy, policy_from_meta, apply_ttl, propagate_lifecycle)`.)

- [ ] **Step 5: Update `namespace.py` call sites**

In `src/gpdatacached/namespace.py`:
- `_write_scalar_meta` (line ~251): replace `apply_lifecycle(r, obj_key, "", "", False, policy)` with `apply_ttl(r, [obj_key], policy)`, and change the import `from .lifecycle import apply_lifecycle` → `from .lifecycle import apply_ttl`.
- `__setitem__` container branch (line ~371): replace `apply_lifecycle(r, obj_key, raw_key, ref_key, True, policy)` with `apply_ttl(r, [obj_key, raw_key, ref_key], policy)`, and that site's import `from .lifecycle import apply_lifecycle` → `from .lifecycle import apply_ttl`.

- [ ] **Step 6: Update `types._create_anonymous_object`**

In `src/gpdatacached/types.py` (around line 200), replace:

```python
    apply_lifecycle(r, obj_key, raw_key, ref_key, entry.is_container, policy)
```

with:

```python
    keys = [obj_key]
    if entry.is_container:
        keys.extend([raw_key, ref_key])
    apply_ttl(r, keys, policy)
```

and change the import at line ~166 `from .lifecycle import apply_lifecycle` → `from .lifecycle import apply_ttl`.

- [ ] **Step 7: Run the full suite**

Run: `./venv/bin/python -m pytest -q`
Expected: all PASS.

- [ ] **Step 8: Lint + format**

Run: `./venv/bin/python -m ruff check src tests && ./venv/bin/python -m ruff format src tests`
Expected: clean (format may rewrite a few files — re-add if so).

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "refactor: collapse apply_policy/apply_lifecycle into apply_ttl"
```

---

## Task 3: Fix sliding-TTL refresh (F1) + strengthen sliding tests (F2)

**Files:**
- Modify: `src/gpdatacached/lifecycle.py` (add `propagate_ttl_refresh`; delete `policy_for_sliding_touch`)
- Modify: `src/gpdatacached/objects.py` (add `refresh_ttl`)
- Modify: `src/gpdatacached/container.py` (`_touch`, `_propagate_sliding_ttl_upward`)
- Modify: `src/gpdatacached/scalar.py` (value setter)
- Modify: `tests/test_lifecycle.py` (remove `TestPolicyForSlidingTouch`; add strong refresh tests)

This is the core correctness fix. `expire_seconds` is still a field here; `refresh_ttl` works either way.

- [ ] **Step 1: Write the failing tests**

In `tests/test_lifecycle.py`, delete the entire `TestPolicyForSlidingTouch` class (lines ~88-107) and its import of `policy_for_sliding_touch` from the top-of-file import list.

Add this new test class (it asserts the TTL actually resets, not merely `> 0`):

```python
class TestSlidingRefresh:
    def test_scalar_sliding_refresh_resets_ttl(self, clean_redis):
        from gpdatacached.config import GPDCDomainConfig
        from gpdatacached.domain import GPDCDomain
        from gpdatacached.keys import make_object_key

        cfg = GPDCDomainConfig(
            redis_host="127.0.0.1", redis_port=6379, redis_db=1,
            redis_password="123456", gc_enabled=False,
        )
        d = GPDCDomain("slide_scalar", cfg)
        try:
            ns = d.ensure_namespace("ns")
            ns["s"] = 42
            obj = ns.get_object("s")
            obj.update_cache_mode_and_ttl("sliding", 10)
            key = make_object_key("slide_scalar", "ns", "s")

            import time
            time.sleep(1.2)
            before = d.redis.ttl(key)
            assert before < 10  # has decayed

            obj.value = 99  # mutation triggers sliding refresh
            after = d.redis.ttl(key)
            assert after > before + 1  # refreshed back toward 10
        finally:
            d.close()

    def test_self_owned_container_cascades_to_owned_children(self, clean_redis):
        from gpdatacached.config import GPDCDomainConfig
        from gpdatacached.domain import GPDCDomain
        from gpdatacached.keys import make_object_key, make_raw_key

        cfg = GPDCDomainConfig(
            redis_host="127.0.0.1", redis_port=6379, redis_db=1,
            redis_password="123456", gc_enabled=False,
            default_cache_mode="sliding", default_ttl_seconds=10,
        )
        d = GPDCDomain("slide_cascade", cfg)
        try:
            ns = d.ensure_namespace("ns")
            ns["d"] = {"items": [1, 2, 3]}
            raw = d.redis.hgetall(make_raw_key("slide_cascade", "ns", "d"))
            anon_enc = next(v for v in raw.values() if ":obj:~" in v)
            anon_name = anon_enc.split(":obj:")[1]
            anon_raw = make_raw_key("slide_cascade", "ns", anon_name)

            import time
            time.sleep(1.2)
            before = d.redis.ttl(anon_raw)
            assert before < 10

            ns["d"]["b"] = "x"  # mutate parent -> cascade refresh
            after = d.redis.ttl(anon_raw)
            assert after > before + 1
        finally:
            d.close()
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_lifecycle.py::TestSlidingRefresh -q`
Expected: FAIL (TTL does not refresh; `after > before + 1` is False).

Also run the existing weak test to confirm it still passes for now:
Run: `./venv/bin/python -m pytest "tests/test_lifecycle.py::TestSlidingNestedContainer" -q`
Expected: PASS.

- [ ] **Step 3: Add `propagate_ttl_refresh` to `lifecycle.py`**

In `src/gpdatacached/lifecycle.py`, delete the `policy_for_sliding_touch` function (lines ~57-64). Add at the end of the file:

```python
def propagate_ttl_refresh(r, domain: str, namespace: str, parent_name: str) -> None:
    """Refresh Redis TTL across an owned subtree (sliding-window refresh).

    For each owned child, re-applies THAT child's own policy TTL to its keys
    (no meta rewrite). Owned children whose policy declares no expiry are left
    untouched, but still recursed into so deeper descendants are reached.
    """
    _propagate_ttl_refresh(r, domain, namespace, parent_name, set())


def _propagate_ttl_refresh(r, domain: str, namespace: str, parent_name: str, visited: set[str]) -> None:
    parent_obj_key = make_object_key(domain, namespace, parent_name)
    parent_meta = ObjectMeta.load(r, parent_obj_key)
    if parent_meta is None or not parent_meta.is_collection():
        return

    for child_name in dict.fromkeys(collect_ref_names_from_raw(r, parent_meta.value, parent_meta.type)):
        if child_name in visited:
            continue
        visited.add(child_name)

        child_obj_key = make_object_key(domain, namespace, child_name)
        child_meta = ObjectMeta.load(r, child_obj_key)
        if child_meta is None or child_meta.owner != parent_name:
            continue

        child_policy = policy_from_meta(child_meta)
        if child_policy.expire_seconds is not None:
            keys = [child_obj_key]
            if child_meta.is_collection():
                keys.append(child_meta.value)
                keys.append(make_ref_key(domain, namespace, child_name))
            apply_ttl(r, keys, child_policy)

        _propagate_ttl_refresh(r, domain, namespace, child_name, visited)
```

- [ ] **Step 4: Add `refresh_ttl()` to `GPDCDataObject`**

In `src/gpdatacached/objects.py`, add this method to `GPDCDataObject` (place it immediately after `set_lifecycle`):

```python
    def refresh_ttl(self) -> None:
        """Re-apply this object's own TTL to its keys and cascade-refresh its owned subtree.

        Used by the sliding-window refresh path on mutation. No-op for objects
        whose policy declares no expiry (permanent / zero ttl).
        """
        from .lifecycle import apply_ttl, propagate_ttl_refresh, policy_from_meta

        ctx = self._load_lifecycle_context()
        policy = policy_from_meta(ctx.meta)
        if policy.expire_seconds is None:
            return
        keys = [ctx.key]
        if ctx.is_collection:
            keys.append(ctx.raw_key)
            keys.append(ctx.ref_key)
        apply_ttl(ctx.r, keys, policy)
        propagate_ttl_refresh(ctx.r, self.domain.name, self.namespace.name, self.canonical_name)
```

- [ ] **Step 5: Rewire `scalar.py` value setter**

In `src/gpdatacached/scalar.py`, replace the tail of the `value` setter:

```python
        current = policy_from_meta(meta)
        new_policy = policy_for_sliding_touch(current)
        if new_policy != current:
            self.set_lifecycle(new_policy)
```

with:

```python
        policy = policy_from_meta(meta)
        if policy.cache_mode == "sliding" and policy.declared_ttl > 0:
            self.refresh_ttl()
```

and update the local import `from .lifecycle import policy_from_meta, policy_for_sliding_touch` → `from .lifecycle import policy_from_meta`.

- [ ] **Step 6: Rewire `container._touch`**

In `src/gpdatacached/container.py`, replace the body of `_touch` (the part after `meta.touch(...).save(r, key)`):

```python
        if meta.owner == self.canonical_name:
            current = policy_from_meta(meta)
            new_policy = policy_for_sliding_touch(current)
            if new_policy != current:
                self.set_lifecycle(new_policy)
        else:
            self._propagate_sliding_ttl_upward(meta.owner, r)
```

with:

```python
        if meta.owner == self.canonical_name:
            policy = policy_from_meta(meta)
            if policy.cache_mode == "sliding" and policy.declared_ttl > 0:
                self.refresh_ttl()
        else:
            self._propagate_sliding_ttl_upward(meta.owner, r)
```

and update the local import in `_touch` `from .lifecycle import policy_from_meta, policy_for_sliding_touch` → `from .lifecycle import policy_from_meta`.

- [ ] **Step 7: Rewire `_propagate_sliding_ttl_upward`**

In `src/gpdatacached/container.py`, replace the body of `_propagate_sliding_ttl_upward`:

```python
    def _propagate_sliding_ttl_upward(self, owner_name: str, r) -> None:
        from .lifecycle import policy_from_meta, policy_for_sliding_touch
        from .keys import make_object_key

        while owner_name:
            owner_obj_key = make_object_key(self.domain.name, self.namespace.name, owner_name)
            owner_meta = ObjectMeta.load(r, owner_obj_key)
            if owner_meta is None:
                break
            if owner_meta.owner == owner_name:
                current = policy_from_meta(owner_meta)
                new_policy = policy_for_sliding_touch(current)
                if new_policy != current:
                    owner_obj = self.namespace.get_object(owner_name)
                    owner_obj.set_lifecycle(new_policy)
                break
            owner_name = owner_meta.owner
```

with:

```python
    def _propagate_sliding_ttl_upward(self, owner_name: str, r) -> None:
        from .lifecycle import policy_from_meta
        from .keys import make_object_key

        while owner_name:
            owner_obj_key = make_object_key(self.domain.name, self.namespace.name, owner_name)
            owner_meta = ObjectMeta.load(r, owner_obj_key)
            if owner_meta is None:
                break
            if owner_meta.owner == owner_name:
                policy = policy_from_meta(owner_meta)
                if policy.cache_mode == "sliding" and policy.declared_ttl > 0:
                    owner_obj = self.namespace.get_object(owner_name)
                    owner_obj.refresh_ttl()
                break
            owner_name = owner_meta.owner
```

- [ ] **Step 8: Verify no stale `policy_for_sliding_touch` references remain**

Run: `rg -n "policy_for_sliding_touch" src tests`
Expected: **no output**.

- [ ] **Step 9: Run the full suite**

Run: `./venv/bin/python -m pytest -q`
Expected: all PASS (including the new strong `TestSlidingRefresh` tests and the existing `TestSlidingNestedContainer`).

- [ ] **Step 10: Lint + format**

Run: `./venv/bin/python -m ruff check src tests && ./venv/bin/python -m ruff format src tests`
Expected: clean.

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "fix: make sliding-window TTL refresh actually fire on mutation"
```

---

## Task 4: `TTLPolicy` derived `expire_seconds` property + two-tier validation (F3, F4)

**Files:**
- Modify: `src/gpdatacached/lifecycle.py` (TTLPolicy, validation, `policy_from_defaults`, delete `_validate_policy`)
- Modify: `src/gpdatacached/objects.py` (`set_lifecycle`, `update_cache_mode_and_ttl`, `expire_in`, `expire_at`, `persist`)
- Modify: `src/gpdatacached/config.py` (delegate to `validate_cache_mode_and_ttl`)
- Modify: `tests/test_lifecycle.py`, `tests/test_objects.py` (drop `expire_seconds` from constructions; migrate `_validate_policy` tests)

- [ ] **Step 1: Update tests first**

In `tests/test_lifecycle.py`:
- Remove every `expire_seconds=...` keyword from `TTLPolicy(...)` constructions across the file (e.g. `TestApplyTtl`, and any in `TestSlidingRefresh` added in Task 3 — note those tests use `update_cache_mode_and_ttl`, not direct construction, so likely none).
- Delete the four `test_validate_policy_rejects_*` functions. Replace them with:

```python
def test_construction_rejects_invalid_mode():
    from gpdatacached.lifecycle import TTLPolicy
    with pytest.raises(ValueError):
        TTLPolicy(cache_mode="invalid", declared_ttl=0)


def test_construction_rejects_negative_ttl():
    from gpdatacached.lifecycle import TTLPolicy
    with pytest.raises(ValueError):
        TTLPolicy(cache_mode="permanent", declared_ttl=-1)


def test_construction_tolerates_ttl_mode_with_zero_ttl():
    # value object only does basic validation; strictness lives in policy_from_defaults
    from gpdatacached.lifecycle import TTLPolicy
    p = TTLPolicy(cache_mode="ttl", declared_ttl=0)
    assert p.expire_seconds is None


def test_policy_from_defaults_rejects_ttl_mode_with_zero_ttl():
    with pytest.raises(ValueError, match="requires ttl > 0"):
        policy_from_defaults("ttl", 0)


def test_policy_from_defaults_rejects_sliding_mode_with_zero_ttl():
    with pytest.raises(ValueError, match="requires ttl > 0"):
        policy_from_defaults("sliding", 0)
```

In `tests/test_objects.py`: drop `expire_seconds=...` from the two `TTLPolicy(...)` constructions (lines ~157, ~172), e.g. `TTLPolicy(cache_mode="ttl", declared_ttl=60, expire_seconds=60)` → `TTLPolicy(cache_mode="ttl", declared_ttl=60)`.

In `tests/test_meta.py`: drop `expire_seconds` from any `TTLPolicy(...)` constructions used there.

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_lifecycle.py tests/test_objects.py tests/test_meta.py -q`
Expected: FAIL (`__init__() got an unexpected keyword argument 'expire_seconds'`).

- [ ] **Step 3: Implement the new `TTLPolicy` + validation in `lifecycle.py`**

Replace the `TTLPolicy` dataclass, `_validate_policy`, and `policy_from_defaults` block (lines ~24-48) with:

```python
VALID_CACHE_MODES = frozenset({"permanent", "ttl", "sliding"})


def _validate_basic(cache_mode: str, ttl: int) -> None:
    if cache_mode not in VALID_CACHE_MODES:
        raise ValueError(f"Invalid cache_mode: {cache_mode!r}")
    if ttl < 0:
        raise ValueError(f"ttl must be >= 0: {ttl}")


def validate_cache_mode_and_ttl(cache_mode: str, ttl: int) -> None:
    """Strict validation for user-facing policy creation."""
    _validate_basic(cache_mode, ttl)
    if cache_mode in ("ttl", "sliding") and ttl <= 0:
        raise ValueError(f"cache_mode={cache_mode!r} requires ttl > 0, got {ttl}")


@dataclass(frozen=True)
class TTLPolicy:
    cache_mode: str
    declared_ttl: int

    def __post_init__(self) -> None:
        _validate_basic(self.cache_mode, self.declared_ttl)

    @property
    def expire_seconds(self) -> int | None:
        if self.cache_mode in ("ttl", "sliding") and self.declared_ttl > 0:
            return self.declared_ttl
        return None


def policy_from_defaults(cache_mode: str, ttl: int) -> TTLPolicy:
    validate_cache_mode_and_ttl(cache_mode, ttl)
    return TTLPolicy(cache_mode=cache_mode, declared_ttl=ttl)
```

(`policy_from_meta` is already 2-field compatible once `expire_seconds` is a property — but it currently passes `expire_seconds=...`. Update `policy_from_meta` to:)

```python
def policy_from_meta(meta: ObjectMeta) -> TTLPolicy:
    return TTLPolicy(cache_mode=meta.cache_mode, declared_ttl=meta.ttl)
```

(The `expire_seconds` property now derives the correct value, including `None` for a ttl/sliding meta with `ttl==0` — preserving the legacy-data tolerance.)

- [ ] **Step 4: Update `objects.set_lifecycle`**

In `src/gpdatacached/objects.py`, in `set_lifecycle`: drop the `_validate_policy(policy)` call and its import. Change the `changed` computation to compare `declared_ttl` instead of `expire_seconds`:

```python
        ctx = self._load_lifecycle_context()

        current = policy_from_meta(ctx.meta)
        changed = (
            current.cache_mode != policy.cache_mode
            or current.declared_ttl != policy.declared_ttl
        )
```

and the local import becomes:

```python
        from .lifecycle import (
            policy_from_meta,
            apply_ttl,
            propagate_lifecycle,
        )
```

- [ ] **Step 5: Simplify `objects.update_cache_mode_and_ttl`**

Replace the whole method body with:

```python
    def update_cache_mode_and_ttl(self, cache_mode: str, ttl: int) -> None:
        from .lifecycle import policy_from_defaults

        policy = policy_from_defaults(cache_mode, ttl)
        ctx = self._load_lifecycle_context()
        self._check_owner_permission(ctx.meta)
        self.set_lifecycle(policy)
```

- [ ] **Step 6: Update `objects.expire_in` / `expire_at` / `persist`**

Drop `expire_seconds=...` from their `TTLPolicy(...)` constructions:
- `expire_in`: `policy = TTLPolicy(cache_mode="ttl", declared_ttl=seconds)`
- `expire_at`: `policy = TTLPolicy(cache_mode="ttl", declared_ttl=seconds)`
- `persist`: `policy = TTLPolicy(cache_mode="permanent", declared_ttl=0)`

- [ ] **Step 7: Delegate `config.py` consistency validation to `lifecycle`**

At the top of `src/gpdatacached/config.py`, add:

```python
from .lifecycle import validate_cache_mode_and_ttl
```

**Behavior-preserving constraint:** `GPDCNamespaceConfig.default_ttl_seconds` may be `None`; the original code only raised the consistency error when both fields were set. Keep the existing `_validate_cache_mode` and `_validate_ttl` field validators unchanged, and only replace the two `_validate_ttl_mode_consistency` model validators so the consistency rule delegates to the shared function — but only when both values are non-`None` (namespace) / always (domain, where `default_ttl_seconds` defaults to `0`):

`GPDCNamespaceConfig`:

```python
    @model_validator(mode="after")
    def _validate_ttl_mode_consistency(self) -> GPDCNamespaceConfig:
        if self.default_cache_mode is not None and self.default_ttl_seconds is not None:
            validate_cache_mode_and_ttl(self.default_cache_mode, self.default_ttl_seconds)
        return self
```

`GPDCDomainConfig`:

```python
    @model_validator(mode="after")
    def _validate_ttl_mode_consistency(self) -> GPDCDomainConfig:
        validate_cache_mode_and_ttl(self.default_cache_mode, self.default_ttl_seconds)
        return self
```

(Leave the two classes' `_validate_cache_mode` / `_validate_ttl` field validators as they are — they already match `_validate_basic` and are evaluated before the model validator. The DRY win here is the single source for the "ttl/sliding requires ttl>0" rule.)

- [ ] **Step 8: Run the full suite**

Run: `./venv/bin/python -m pytest -q`
Expected: all PASS. Pay attention to `TestPolicyFromMetaInherited` / `test_ttl_meta_with_zero_ttl` — these must still pass (tolerance preserved).

- [ ] **Step 9: Verify no stale `expire_seconds=` construction or `_validate_policy` remains**

Run: `rg -n "expire_seconds=|_validate_policy" src tests`
Expected: only references to the `expire_seconds` **property reads** (e.g. `policy.expire_seconds`, `child_policy.expire_seconds`, `seconds = policy.expire_seconds`); no `expire_seconds=` keyword args and no `_validate_policy`.

- [ ] **Step 10: Lint + format**

Run: `./venv/bin/python -m ruff check src tests && ./venv/bin/python -m ruff format src tests`
Expected: clean.

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "refactor: derive TTLPolicy.expire_seconds and centralize validation"
```

---

## Task 5: Drop `visited` from the `propagate_lifecycle` signature (F7)

**Files:**
- Modify: `src/gpdatacached/lifecycle.py` (`propagate_lifecycle` → public wrapper + private recursion)
- Modify: `src/gpdatacached/objects.py` (call site drops `set()`)
- Modify: `tests/test_lifecycle.py` (call site drops `set()`)

- [ ] **Step 1: Update the test call site**

In `tests/test_lifecycle.py`, the `test_propagate_lifecycle_uses_owner_check` call (line ~230):

```python
    propagate_lifecycle(r, "d", "ns", parent_name, policy, set())
```

becomes:

```python
    propagate_lifecycle(r, "d", "ns", parent_name, policy)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_lifecycle.py::test_propagate_lifecycle_uses_owner_check -q`
Expected: FAIL (`TypeError: propagate_lifecycle() takes 6 positional arguments but 5 were given` — signature mismatch once changed; or it fails now because the public function still requires `visited`).

- [ ] **Step 3: Refactor `propagate_lifecycle` in `lifecycle.py`**

Rename the current `propagate_lifecycle` function to `_propagate_lifecycle` (keeping its `(r, domain, namespace, parent_name, policy, visited)` signature and body, but replace its child-name loop header so per-level dedup removes the need for `visited` to guard same-level duplicates — keep `visited` only as a defensive cross-level guard). Add a new public wrapper:

```python
def propagate_lifecycle(r, domain: str, namespace: str, parent_name: str, policy: TTLPolicy) -> None:
    """Propagate a policy change to an owned subtree (meta rewrite + TTL apply)."""
    _propagate_lifecycle(r, domain, namespace, parent_name, policy, set())
```

In the private `_propagate_lifecycle`, change:

```python
    for child_name in child_names:
```

to:

```python
    for child_name in dict.fromkeys(child_names):
```

so duplicate ref names within one level are deduped locally. (Keep the `if child_name in visited: continue` defensive guard.)

- [ ] **Step 4: Update the `objects.set_lifecycle` call site**

In `src/gpdatacached/objects.py`, in `set_lifecycle`, drop the `set()` argument:

```python
            propagate_lifecycle(
                ctx.r,
                self.domain.name,
                self.namespace.name,
                self.canonical_name,
                policy,
            )
```

- [ ] **Step 5: Run the full suite**

Run: `./venv/bin/python -m pytest -q`
Expected: all PASS.

- [ ] **Step 6: Lint + format + commit**

```bash
./venv/bin/python -m ruff check src tests && ./venv/bin/python -m ruff format src tests
git add -A
git commit -m "refactor: hide propagate_lifecycle visited guard as an internal detail"
```

---

## Task 6: Relocate `_LifecycleContext` to `objects.py` (F6)

**Files:**
- Modify: `src/gpdatacached/objects.py` (define `_LifecycleContext` locally)
- Modify: `src/gpdatacached/lifecycle.py` (remove the class)

- [ ] **Step 1: Move the class into `objects.py`**

In `src/gpdatacached/objects.py`, remove the top-level import `from .lifecycle import _LifecycleContext` (line 14). Add the `NamedTuple` definition near the top of the file (after the other imports), with its needed import:

```python
from typing import NamedTuple
```

and:

```python
class _LifecycleContext(NamedTuple):
    r: object
    key: str
    meta: ObjectMeta
    raw_key: str
    ref_key: str
    is_collection: bool
```

- [ ] **Step 2: Remove the class from `lifecycle.py`**

In `src/gpdatacached/lifecycle.py`, delete the `_LifecycleContext` class definition (lines ~15-21) and the now-unused `NamedTuple` import if nothing else uses it. (`from typing import TYPE_CHECKING, NamedTuple` → `from typing import TYPE_CHECKING` if `NamedTuple` is no longer used.)

- [ ] **Step 3: Verify nothing else imports `_LifecycleContext` from lifecycle**

Run: `rg -n "_LifecycleContext" src tests`
Expected: only the definition + use inside `objects.py`; no `from .lifecycle import _LifecycleContext`.

- [ ] **Step 4: Run the full suite + lint**

Run: `./venv/bin/python -m pytest -q && ./venv/bin/python -m ruff check src tests`
Expected: all PASS, lint clean.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: move _LifecycleContext into objects.py"
```

---

## Final verification

- [ ] **Full suite green:** `./venv/bin/python -m pytest -q`
- [ ] **Lint clean:** `./venv/bin/python -m ruff check src tests`
- [ ] **Format clean:** `./venv/bin/python -m ruff format --check src tests`
- [ ] **No stale names:** `rg -n "policy_for_sliding_touch|_validate_policy|apply_lifecycle|apply_policy|gpdatacached\.ttl|from \.ttl import|expire_seconds=" src tests` → empty.
- [ ] **Sliding refresh confirmed:** the `TestSlidingRefresh` tests assert TTL actually resets.
