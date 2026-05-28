# P0-2 Inherited Lifecycle + P2-1 Lifecycle Convergence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix container TTL/sliding lifecycle inconsistency by introducing `cache_mode="inherited"` for anonymous nested objects, unified lifecycle enforcement covering `obj:`, `raw:`, `ref:`, and parent-to-child propagation.

**Architecture:** Introduce a pydantic `ObjectMeta` model for typed metadata, expand `cache_mode` to include `"inherited"`, compute effective policies from parent objects at runtime, and consolidate all lifecycle enforcement into a single internal entry point.

**Tech Stack:** Python 3.12+, pydantic v2 (already available), Redis, pytest

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/gpdatacached/meta.py` | **New** — `ObjectMeta` pydantic model, `effective_policy()`, `apply_lifecycle()`, `propagate_lifecycle()` |
| `src/gpdatacached/ttl.py` | Expand to accept `"inherited"` in `policy_from_meta()`, update `policy_from_defaults()` validation |
| `src/gpdatacached/types.py` | Anonymous object creation uses `"inherited"`, calls unified lifecycle |
| `src/gpdatacached/_base.py` | Setters handle `"inherited"` override, use ObjectMeta, call unified lifecycle |
| `src/gpdatacached/container.py` | `_touch()` calls unified lifecycle with propagation |
| `src/gpdatacached/scalar.py` | Value setter uses ObjectMeta, calls unified lifecycle |
| `src/gpdatacached/namespace.py` | `_write_scalar_meta` / `_write_container_meta` use ObjectMeta and unified lifecycle |
| `tests/test_meta.py` | **New** — ObjectMeta unit tests |
| `tests/test_inherited_lifecycle.py` | **New** — Inherited lifecycle integration tests |

---

### Task 1: Create ObjectMeta pydantic model

**Files:**
- Create: `src/gpdatacached/meta.py`
- Create: `tests/test_meta.py`

- [ ] **Step 1: Write failing tests for ObjectMeta**

```python
# tests/test_meta.py
import datetime
import pytest

from gpdatacached.meta import ObjectMeta


class TestObjectMetaFromRedis:
    def test_parse_full_container_meta(self):
        data = {
            "type": "collection:list",
            "codec": "native",
            "value": "gpdc:d:ns:raw:~anon:abc",
            "cache_mode": "inherited",
            "ttl": "300",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "item_count": "3",
        }
        meta = ObjectMeta.from_redis(data)
        assert meta.type == "collection:list"
        assert meta.codec == "native"
        assert meta.value == "gpdc:d:ns:raw:~anon:abc"
        assert meta.cache_mode == "inherited"
        assert meta.ttl == 300
        assert meta.item_count == 3
        assert meta.created_at is not None
        assert meta.updated_at is not None

    def test_parse_minimal_scalar_meta(self):
        data = {
            "type": "scalar:int",
            "value": "int:42",
            "cache_mode": "permanent",
        }
        meta = ObjectMeta.from_redis(data)
        assert meta.type == "scalar:int"
        assert meta.cache_mode == "permanent"
        assert meta.ttl == 0
        assert meta.item_count is None

    def test_parse_empty_meta_defaults_to_permanent(self):
        meta = ObjectMeta.from_redis({})
        assert meta.cache_mode == "permanent"
        assert meta.ttl == 0

    def test_invalid_cache_mode_raises(self):
        with pytest.raises(Exception):
            ObjectMeta.from_redis({"type": "scalar:int", "cache_mode": "bogus"})

    def test_negative_ttl_raises(self):
        with pytest.raises(Exception):
            ObjectMeta.from_redis({"type": "scalar:int", "cache_mode": "ttl", "ttl": "-1"})


class TestObjectMetaToRedisMapping:
    def test_roundtrip_container(self):
        data = {
            "type": "collection:dict",
            "codec": "native",
            "value": "gpdc:d:ns:raw:x",
            "cache_mode": "ttl",
            "ttl": "60",
            "item_count": "2",
        }
        meta = ObjectMeta.from_redis(data)
        mapping = meta.to_redis_mapping()
        assert mapping["type"] == "collection:dict"
        assert mapping["cache_mode"] == "ttl"
        assert mapping["ttl"] == "60"
        assert mapping["item_count"] == "2"

    def test_roundtrip_scalar(self):
        data = {
            "type": "scalar:str",
            "value": "str:hello",
            "cache_mode": "permanent",
        }
        meta = ObjectMeta.from_redis(data)
        mapping = meta.to_redis_mapping()
        assert "item_count" not in mapping


class TestObjectMetaHelpers:
    def test_is_collection_true(self):
        meta = ObjectMeta.from_redis({"type": "collection:list", "cache_mode": "permanent"})
        assert meta.is_collection() is True

    def test_is_collection_false(self):
        meta = ObjectMeta.from_redis({"type": "scalar:int", "cache_mode": "permanent"})
        assert meta.is_collection() is False

    def test_is_inherited_true(self):
        meta = ObjectMeta.from_redis({"type": "collection:list", "cache_mode": "inherited"})
        assert meta.is_inherited() is True

    def test_is_inherited_false(self):
        meta = ObjectMeta.from_redis({"type": "collection:list", "cache_mode": "ttl"})
        assert meta.is_inherited() is False

    def test_declares_expiry_true(self):
        meta = ObjectMeta.from_redis({"type": "scalar:int", "cache_mode": "ttl", "ttl": "300"})
        assert meta.declares_expiry() is True

    def test_declares_expiry_false_permanent(self):
        meta = ObjectMeta.from_redis({"type": "scalar:int", "cache_mode": "permanent"})
        assert meta.declares_expiry() is False

    def test_declares_expiry_false_inherited(self):
        meta = ObjectMeta.from_redis({"type": "collection:list", "cache_mode": "inherited"})
        assert meta.declares_expiry() is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_meta.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gpdatacached.meta'`

- [ ] **Step 3: Write ObjectMeta implementation**

```python
# src/gpdatacached/meta.py
from __future__ import annotations

import datetime
from typing import Literal

from pydantic import BaseModel, model_validator


class ObjectMeta(BaseModel):
    type: str
    codec: str = "native"
    value: str = ""
    cache_mode: Literal["permanent", "ttl", "sliding", "inherited"] = "permanent"
    ttl: int = 0
    created_at: datetime.datetime | None = None
    updated_at: datetime.datetime | None = None
    item_count: int | None = None

    @model_validator(mode="after")
    def _validate_ttl(self) -> ObjectMeta:
        if self.ttl < 0:
            raise ValueError(f"ttl must be >= 0, got {self.ttl}")
        return self

    @classmethod
    def from_redis(cls, data: dict) -> ObjectMeta:
        cache_mode = data.get("cache_mode") or "permanent"
        ttl_str = data.get("ttl", "")
        ttl = int(ttl_str) if ttl_str else 0
        created_at_str = data.get("created_at")
        updated_at_str = data.get("updated_at")
        item_count_str = data.get("item_count")
        return cls(
            type=data.get("type", ""),
            codec=data.get("codec", "native"),
            value=data.get("value", ""),
            cache_mode=cache_mode,
            ttl=ttl,
            created_at=datetime.datetime.fromisoformat(created_at_str) if created_at_str else None,
            updated_at=datetime.datetime.fromisoformat(updated_at_str) if updated_at_str else None,
            item_count=int(item_count_str) if item_count_str is not None else None,
        )

    def to_redis_mapping(self) -> dict[str, str]:
        mapping: dict[str, str] = {
            "type": self.type,
            "codec": self.codec,
            "value": self.value,
            "cache_mode": self.cache_mode,
            "ttl": str(self.ttl) if self.ttl else "",
            "updated_at": self.updated_at.isoformat() if self.updated_at else "",
        }
        if self.item_count is not None:
            mapping["item_count"] = str(self.item_count)
        if self.created_at is not None:
            mapping["created_at"] = self.created_at.isoformat()
        return mapping

    def is_collection(self) -> bool:
        return self.type.startswith("collection:")

    def is_inherited(self) -> bool:
        return self.cache_mode == "inherited"

    def declares_expiry(self) -> bool:
        return self.cache_mode in ("ttl", "sliding") and self.ttl > 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/test_meta.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/gpdatacached/meta.py tests/test_meta.py
git commit -m "feat: add ObjectMeta pydantic model for typed metadata"
```

---

### Task 2: Expand ttl.py to accept "inherited" and add unified lifecycle functions

**Files:**
- Modify: `src/gpdatacached/ttl.py`
- Modify: `tests/test_ttl_policy.py`

- [ ] **Step 1: Write failing tests for "inherited" in policy_from_meta**

```python
# Add to tests/test_ttl_policy.py

class TestPolicyFromMetaInherited:
    def test_inherited_meta_returns_inherited_mode(self):
        p = policy_from_meta({"cache_mode": "inherited", "ttl": "300"})
        assert p.cache_mode == "inherited"
        assert p.declared_ttl == 300

    def test_inherited_meta_no_expire_seconds(self):
        p = policy_from_meta({"cache_mode": "inherited", "ttl": "300"})
        assert p.expire_seconds is None

    def test_inherited_zero_ttl(self):
        p = policy_from_meta({"cache_mode": "inherited", "ttl": "0"})
        assert p.cache_mode == "inherited"
        assert p.declared_ttl == 0
        assert p.expire_seconds is None


class TestPolicyFromDefaultsRejectsInherited:
    def test_inherited_rejected_in_defaults(self):
        with pytest.raises(ValueError, match="Invalid cache_mode"):
            policy_from_defaults("inherited", 300)


class TestPolicyForCacheModeChangeRejectsInherited:
    def test_change_to_inherited_rejected(self):
        current = TTLPolicy(cache_mode="ttl", declared_ttl=300, expire_seconds=300)
        with pytest.raises(ValueError, match="Invalid cache_mode"):
            policy_for_cache_mode_change(current, "inherited")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_ttl_policy.py::TestPolicyFromMetaInherited -v`
Expected: FAIL — `policy_from_meta` doesn't accept `"inherited"`

- [ ] **Step 3: Update ttl.py**

In `ttl.py`, modify `policy_from_meta` to accept `"inherited"`:

```python
def policy_from_meta(meta: dict) -> TTLPolicy:
    cache_mode = meta.get("cache_mode") or "permanent"
    ttl_str = meta.get("ttl", "")
    declared_ttl = int(ttl_str) if ttl_str else 0
    if cache_mode in ("ttl", "sliding") and declared_ttl > 0:
        return TTLPolicy(cache_mode=cache_mode, declared_ttl=declared_ttl, expire_seconds=declared_ttl)
    return TTLPolicy(cache_mode=cache_mode, declared_ttl=declared_ttl, expire_seconds=None)
```

The key change: `policy_from_meta` already works for `"inherited"` because it falls through to the `return` at the bottom with `expire_seconds=None`. We just need to verify the existing code handles it. The only code change is that `"inherited"` is now a valid value that passes through without error.

No code change needed in `policy_from_meta` itself — it already handles unknown modes gracefully. But we need to update `policy_from_defaults` and `policy_for_cache_mode_change` to explicitly validate against `"inherited"`:

The current `policy_from_defaults` already raises for `"inherited"` because its check is `if cache_mode not in ("permanent", "ttl", "sliding")`. Same for `policy_for_cache_mode_change`. So **no code changes needed in ttl.py for this task** — the existing validation already works correctly.

But we should still run the new tests to confirm the behavior matches expectations.

- [ ] **Step 4: Run all TTL tests to verify**

Run: `./venv/bin/python -m pytest tests/test_ttl_policy.py -v`
Expected: All PASS (inherited passes through policy_from_meta, rejected by policy_from_defaults)

- [ ] **Step 5: Commit**

```bash
git add tests/test_ttl_policy.py
git commit -m "test: add inherited cache_mode tests for ttl policy"
```

---

### Task 3: Add unified lifecycle functions to meta.py

**Files:**
- Modify: `src/gpdatacached/meta.py`
- Modify: `tests/test_meta.py`

- [ ] **Step 1: Write failing tests for unified lifecycle functions**

```python
# Add to tests/test_meta.py

from unittest.mock import MagicMock, call
from gpdatacached.ttl import TTLPolicy


class TestApplyLifecycle:
    def test_applies_ttl_to_obj_key(self):
        r = MagicMock()
        policy = TTLPolicy(cache_mode="ttl", declared_ttl=300, expire_seconds=300)
        apply_lifecycle(r, "gpdc:d:ns:obj:x", "gpdc:d:ns:raw:x", "gpdc:d:ns:ref:x", True, policy)
        r.expire.assert_any_call("gpdc:d:ns:obj:x", 300)

    def test_applies_ttl_to_raw_and_ref_for_container(self):
        r = MagicMock()
        policy = TTLPolicy(cache_mode="ttl", declared_ttl=300, expire_seconds=300)
        apply_lifecycle(r, "gpdc:d:ns:obj:x", "gpdc:d:ns:raw:x", "gpdc:d:ns:ref:x", True, policy)
        assert call("gpdc:d:ns:obj:x", 300) in r.expire.call_args_list
        assert call("gpdc:d:ns:raw:x", 300) in r.expire.call_args_list
        assert call("gpdc:d:ns:ref:x", 300) in r.expire.call_args_list

    def test_persist_all_keys(self):
        r = MagicMock()
        policy = TTLPolicy(cache_mode="permanent", declared_ttl=0, expire_seconds=None)
        apply_lifecycle(r, "gpdc:d:ns:obj:x", "gpdc:d:ns:raw:x", "gpdc:d:ns:ref:x", True, policy)
        r.persist.assert_any_call("gpdc:d:ns:obj:x")
        r.persist.assert_any_call("gpdc:d:ns:raw:x")
        r.persist.assert_any_call("gpdc:d:ns:ref:x")

    def test_scalar_skips_raw_and_ref(self):
        r = MagicMock()
        policy = TTLPolicy(cache_mode="ttl", declared_ttl=300, expire_seconds=300)
        apply_lifecycle(r, "gpdc:d:ns:obj:x", "", "", False, policy)
        r.expire.assert_called_once_with("gpdc:d:ns:obj:x", 300)

    def test_container_raw_missing_still_applies_to_obj_and_ref(self):
        r = MagicMock()
        r.exists.return_value = False
        policy = TTLPolicy(cache_mode="ttl", declared_ttl=300, expire_seconds=300)
        apply_lifecycle(r, "gpdc:d:ns:obj:x", "gpdc:d:ns:raw:x", "gpdc:d:ns:ref:x", True, policy)
        r.expire.assert_any_call("gpdc:d:ns:obj:x", 300)
        r.expire.assert_any_call("gpdc:d:ns:ref:x", 300)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_meta.py::TestApplyLifecycle -v`
Expected: FAIL — `ImportError` for `apply_lifecycle`

- [ ] **Step 3: Implement apply_lifecycle in meta.py**

Add to `src/gpdatacached/meta.py`:

```python
def apply_lifecycle(
    r,
    obj_key: str,
    raw_key: str,
    ref_key: str,
    is_collection: bool,
    policy,
) -> None:
    from .ttl import apply_policy

    apply_policy(r, obj_key, policy)
    if is_collection:
        if raw_key:
            apply_policy(r, raw_key, policy)
        if ref_key:
            apply_policy(r, ref_key, policy)
```

Update the import in the test file — `apply_lifecycle` comes from `gpdatacached.meta`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/test_meta.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/gpdatacached/meta.py tests/test_meta.py
git commit -m "feat: add apply_lifecycle for unified TTL enforcement on obj/raw/ref"
```

---

### Task 4: Add effective_policy computation

**Files:**
- Modify: `src/gpdatacached/meta.py`
- Modify: `tests/test_meta.py`

- [ ] **Step 1: Write failing tests for effective_policy**

```python
# Add to tests/test_meta.py

class TestEffectivePolicy:
    def test_non_inherited_returns_own_policy(self):
        meta = ObjectMeta.from_redis({"type": "collection:list", "cache_mode": "ttl", "ttl": "300"})
        r = MagicMock()
        policy = effective_policy(meta, r, "d", "ns")
        assert policy.cache_mode == "ttl"
        assert policy.expire_seconds == 300

    def test_permanent_non_inherited(self):
        meta = ObjectMeta.from_redis({"type": "scalar:int", "cache_mode": "permanent"})
        r = MagicMock()
        policy = effective_policy(meta, r, "d", "ns")
        assert policy.cache_mode == "permanent"
        assert policy.expire_seconds is None

    def test_inherited_with_permanent_parent(self):
        meta = ObjectMeta.from_redis({"type": "collection:list", "cache_mode": "inherited", "ttl": "0"})
        r = MagicMock()
        r.smembers.return_value = {"parent1"}
        r.hgetall.return_value = {
            "type": "collection:dict",
            "cache_mode": "permanent",
            "ttl": "",
        }
        policy = effective_policy(meta, r, "d", "ns")
        assert policy.cache_mode == "permanent"
        assert policy.expire_seconds is None

    def test_inherited_with_ttl_parent(self):
        meta = ObjectMeta.from_redis({"type": "collection:list", "cache_mode": "inherited", "ttl": "0"})
        r = MagicMock()
        r.smembers.return_value = {"parent1"}
        r.hgetall.return_value = {
            "type": "collection:dict",
            "cache_mode": "ttl",
            "ttl": "300",
        }
        r.ttl.return_value = 250
        policy = effective_policy(meta, r, "d", "ns")
        assert policy.cache_mode == "ttl"
        assert policy.expire_seconds == 250

    def test_inherited_with_no_parents(self):
        meta = ObjectMeta.from_redis({"type": "collection:list", "cache_mode": "inherited", "ttl": "0"})
        r = MagicMock()
        r.smembers.return_value = set()
        policy = effective_policy(meta, r, "d", "ns")
        assert policy.expire_seconds is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_meta.py::TestEffectivePolicy -v`
Expected: FAIL — `ImportError` for `effective_policy`

- [ ] **Step 3: Implement effective_policy**

Add to `src/gpdatacached/meta.py`:

```python
def effective_policy(
    meta: ObjectMeta,
    r,
    domain: str,
    namespace: str,
) -> "TTLPolicy":
    from .keys import make_ref_key, make_object_key
    from .ttl import TTLPolicy, policy_from_meta

    if not meta.is_inherited():
        return policy_from_meta(meta.to_redis_mapping())

    ref_key = make_ref_key(domain, namespace, meta._name) if hasattr(meta, "_name") else ""
    obj_prefix = f"gpdc:{domain}:{namespace}:obj:"
    referrers = r.smembers(ref_key) if ref_key else set()

    best_policy = None
    best_remaining = -2

    for ref_name in referrers:
        parent_obj_key = obj_prefix + ref_name
        parent_meta_data = r.hgetall(parent_obj_key)
        if not parent_meta_data:
            continue
        parent_meta = ObjectMeta.from_redis(parent_meta_data)
        parent_policy = policy_from_meta(parent_meta_data)

        if parent_policy.cache_mode == "permanent":
            return TTLPolicy(cache_mode="permanent", declared_ttl=0, expire_seconds=None)

        if parent_policy.expire_seconds is not None:
            remaining = r.ttl(parent_obj_key)
            if remaining > best_remaining:
                best_remaining = remaining
                best_policy = parent_policy

    if best_policy is not None:
        return TTLPolicy(
            cache_mode=best_policy.cache_mode,
            declared_ttl=best_policy.declared_ttl,
            expire_seconds=best_remaining if best_remaining > 0 else best_policy.expire_seconds,
        )

    return TTLPolicy(cache_mode="permanent", declared_ttl=0, expire_seconds=None)
```

**Important:** `effective_policy` needs the object's name to look up its ref set. The `ObjectMeta` model doesn't store the object name. We need to pass it explicitly. Let me revise the design:

The function signature should be:

```python
def effective_policy(
    meta: ObjectMeta,
    r,
    domain: str,
    namespace: str,
    obj_name: str,
) -> TTLPolicy:
```

And tests should be updated to pass `obj_name`.

- [ ] **Step 4: Update test signatures and run**

Run: `./venv/bin/python -m pytest tests/test_meta.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/gpdatacached/meta.py tests/test_meta.py
git commit -m "feat: add effective_policy for inherited object lifecycle computation"
```

---

### Task 5: Add propagate_lifecycle function

**Files:**
- Modify: `src/gpdatacached/meta.py`
- Modify: `tests/test_meta.py`

- [ ] **Step 1: Write failing test for propagation**

```python
# Add to tests/test_meta.py

class TestPropagateLifecycle:
    def test_propagate_refreshes_inherited_children(self):
        r = MagicMock()
        child_obj_key = "gpdc:d:ns:obj:~anon:child1"
        child_raw_key = "gpdc:d:ns:raw:~anon:child1"
        child_ref_key = "gpdc:d:ns:ref:~anon:child1"

        r.smembers.return_value = set()
        r.hgetall.return_value = {
            "type": "collection:list",
            "cache_mode": "inherited",
            "ttl": "0",
            "value": "gpdc:d:ns:raw:~anon:child1",
        }
        r.ttl.return_value = 200

        propagate_lifecycle(r, "d", "ns", "parent", set())
        r.hset.assert_called()

    def test_propagate_skips_non_inherited_children(self):
        r = MagicMock()
        r.hgetall.return_value = {
            "type": "collection:list",
            "cache_mode": "ttl",
            "ttl": "600",
            "value": "gpdc:d:ns:raw:~anon:child1",
        }
        propagate_lifecycle(r, "d", "ns", "parent", set())
        r.expire.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_meta.py::TestPropagateLifecycle -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement propagate_lifecycle**

Add to `src/gpdatacached/meta.py`:

```python
def propagate_lifecycle(
    r,
    domain: str,
    namespace: str,
    parent_name: str,
    visited: set[str],
) -> None:
    from .keys import make_object_key, make_raw_key, make_ref_key
    from .namespace import GPDCNamespace

    if parent_name in visited:
        return
    visited.add(parent_name)

    parent_obj_key = make_object_key(domain, namespace, parent_name)
    parent_meta_data = r.hgetall(parent_obj_key)
    if not parent_meta_data:
        return
    parent_meta = ObjectMeta.from_redis(parent_meta_data)
    if not parent_meta.is_collection():
        return

    child_names = _collect_child_names(r, parent_meta.value, parent_meta.type)
    for child_name in child_names:
        if not child_name.startswith("~"):
            continue
        child_obj_key = make_object_key(domain, namespace, child_name)
        child_meta_data = r.hgetall(child_obj_key)
        if not child_meta_data:
            continue
        child_meta = ObjectMeta.from_redis(child_meta_data)
        if not child_meta.is_inherited():
            continue

        child_raw_key = child_meta.value if child_meta.is_collection() else ""
        child_ref_key = make_ref_key(domain, namespace, child_name)

        eff = effective_policy(child_meta, r, domain, namespace, child_name)
        apply_lifecycle(r, child_obj_key, child_raw_key, child_ref_key, child_meta.is_collection(), eff)
        propagate_lifecycle(r, domain, namespace, child_name, visited)


def _collect_child_names(r, raw_key: str, raw_type: str) -> list[str]:
    if not raw_key:
        return []
    names = []
    if raw_type == "collection:dict":
        for val in r.hvals(raw_key):
            _maybe_add_name(val, names)
    elif raw_type == "collection:list":
        for val in r.lrange(raw_key, 0, -1):
            _maybe_add_name(val, names)
    elif raw_type == "collection:set":
        for val in r.smembers(raw_key):
            _maybe_add_name(val, names)
    return names


def _maybe_add_name(encoded: str, names: list[str]) -> None:
    if isinstance(encoded, str) and encoded.startswith("ref:"):
        obj_key = encoded[len("ref:") :]
        parts = obj_key.split(":obj:")
        if len(parts) == 2:
            names.append(parts[1])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/test_meta.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/gpdatacached/meta.py tests/test_meta.py
git commit -m "feat: add propagate_lifecycle for parent-to-child TTL propagation"
```

---

### Task 6: Update anonymous object creation to use "inherited"

**Files:**
- Modify: `src/gpdatacached/types.py`

- [ ] **Step 1: Write failing test for inherited anonymous creation**

```python
# Add to tests/test_inherited_lifecycle.py (new file)
import pytest
import redis as redis_lib

from gpdatacached.config import GPDCDomainConfig
from gpdatacached.domain import GPDCDomain
from gpdatacached.keys import make_object_key, make_raw_key, make_ref_key

DOMAIN = "testdomain_inherit"
NS = "testns_inherit"
REDIS_HOST = "127.0.0.1"
REDIS_PORT = 6379
REDIS_DB = 1
REDIS_PASSWORD = "123456"


@pytest.fixture()
def r():
    client = redis_lib.Redis(
        host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, password=REDIS_PASSWORD, decode_responses=True
    )
    yield client
    pattern = f"gpdc:{DOMAIN}:{NS}:*"
    cursor = 0
    while True:
        cursor, keys = client.scan(cursor=cursor, match=pattern, count=100)
        if keys:
            client.delete(*keys)
        if cursor == 0:
            break
    client.close()


@pytest.fixture()
def ns(r):
    config = GPDCDomainConfig(
        redis_host=REDIS_HOST, redis_port=REDIS_PORT, redis_db=REDIS_DB, redis_password=REDIS_PASSWORD,
        gc_enabled=False,
    )
    domain = GPDCDomain(DOMAIN, config)
    return domain.get_namespace(NS)


class TestAnonymousInherited:
    def test_anon_object_has_inherited_cache_mode(self, ns, r):
        ns["d"] = {"a": [1, 2, 3]}
        raw_data = r.hgetall(make_raw_key(DOMAIN, NS, "d"))
        anon_name = raw_data["str:a"].split(":obj:")[1]
        anon_obj_key = make_object_key(DOMAIN, NS, anon_name)
        assert r.hget(anon_obj_key, "cache_mode") == "inherited"

    def test_anon_raw_key_has_same_ttl_as_parent(self, ns, r):
        config = GPDCDomainConfig(
            redis_host=REDIS_HOST, redis_port=REDIS_PORT, redis_db=REDIS_DB, redis_password=REDIS_PASSWORD,
            default_cache_mode="ttl", default_ttl_seconds=300, gc_enabled=False,
        )
        domain = GPDCDomain(DOMAIN + "_ttl", config)
        ns_ttl = domain.get_namespace(NS)
        ns_ttl["d"] = {"a": [1, 2, 3]}
        raw_data = r.hgetall(make_raw_key(DOMAIN + "_ttl", NS, "d"))
        anon_name = raw_data["str:a"].split(":obj:")[1]
        anon_obj_key = make_object_key(DOMAIN + "_ttl", NS, anon_name)
        anon_raw_key = make_raw_key(DOMAIN + "_ttl", NS, anon_name)
        anon_ref_key = make_ref_key(DOMAIN + "_ttl", NS, anon_name)

        parent_ttl = r.ttl(make_object_key(DOMAIN + "_ttl", NS, "d"))
        obj_ttl = r.ttl(anon_obj_key)
        raw_ttl = r.ttl(anon_raw_key)
        ref_ttl = r.ttl(anon_ref_key)

        assert obj_ttl > 0
        assert raw_ttl > 0
        assert ref_ttl > 0

        pattern = f"gpdc:{DOMAIN + '_ttl'}:{NS}:*"
        cursor = 0
        while True:
            cursor, keys = r.scan(cursor=cursor, match=pattern, count=100)
            if keys:
                r.delete(*keys)
            if cursor == 0:
                break
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_inherited_lifecycle.py::TestAnonymousInherited::test_anon_object_has_inherited_cache_mode -v`
Expected: FAIL — anon cache_mode is not `"inherited"` yet

- [ ] **Step 3: Modify `_create_anonymous_object` in types.py**

Change `_create_anonymous_object` to:
1. Write `cache_mode="inherited"` instead of copying parent's mode
2. Call `apply_lifecycle` instead of `apply_policy`

```python
def _create_anonymous_object(
    namespace: GPDCNamespace,
    python_value: object,
    parent_name: str,
    cache_mode: str,
    ttl: int,
) -> tuple[str, str]:
    from .ttl import policy_from_defaults, meta_ttl_value
    from .meta import apply_lifecycle
    from .keys import make_ref_key

    entry = GPDCTypeRegistry.lookup_by_python_type(python_value)
    now = datetime.datetime.now(datetime.UTC).isoformat()
    parent_policy = policy_from_defaults(cache_mode, ttl)

    anon_name = f"~anon:{uuid.uuid4()}"
    obj_key = make_object_key(namespace.domain.name, namespace.name, anon_name)
    raw_key = make_raw_key(namespace.domain.name, namespace.name, anon_name)
    ref_key = make_ref_key(namespace.domain.name, namespace.name, anon_name)

    r = namespace._redis()
    mapping = {
        "type": entry.gpdc_type,
        "codec": "native",
        "value": raw_key,
        "cache_mode": "inherited",
        "ttl": "",
        "created_at": now,
        "updated_at": now,
    }
    if entry.is_container:
        mapping["item_count"] = str(len(python_value))
    r.hset(obj_key, mapping=mapping)

    if entry.is_container:
        from .codec import GPDCContainerCodec

        if issubclass(entry.codec_class, GPDCContainerCodec):
            entry.codec_class.create_raw(
                namespace, raw_key, python_value, anon_name, parent_policy.cache_mode, parent_policy.declared_ttl
            )

    r.sadd(ref_key, parent_name)

    apply_lifecycle(r, obj_key, raw_key, ref_key, entry.is_container, parent_policy)

    ref_val = f"ref:{obj_key}"
    return anon_name, ref_val
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/test_inherited_lifecycle.py -v`
Expected: PASS

- [ ] **Step 5: Update the existing test that asserts anon inherits parent cache_mode**

In `tests/test_nested_containers.py`, the test `test_anon_cache_mode_inherits_parent` at line 469 currently asserts `anon_cache_mode == parent_cache_mode`. Since anonymous objects now use `"inherited"`, update this test:

```python
def test_anon_cache_mode_is_inherited(self, ns, r):
    """Anonymous objects use cache_mode='inherited'."""
    ns["d"] = {"a": [1, 2, 3]}
    raw_data = r.hgetall(make_raw_key(DOMAIN, NS, "d"))
    anon_name = raw_data["str:a"].split(":obj:")[1]
    anon_obj_key = make_object_key(DOMAIN, NS, anon_name)
    anon_cache_mode = r.hget(anon_obj_key, "cache_mode")
    assert anon_cache_mode == "inherited"
```

- [ ] **Step 6: Run all tests**

Run: `./venv/bin/python -m pytest -v`
Expected: All PASS (existing tests adapted)

- [ ] **Step 7: Commit**

```bash
git add src/gpdatacached/types.py tests/test_inherited_lifecycle.py tests/test_nested_containers.py
git commit -m "feat: anonymous objects use cache_mode=inherited with unified lifecycle"
```

---

### Task 7: Update namespace.py write methods to use unified lifecycle

**Files:**
- Modify: `src/gpdatacached/namespace.py`

- [ ] **Step 1: Modify `_write_scalar_meta` to use `apply_lifecycle`**

Replace the scattered `apply_policy` call:

```python
def _write_scalar_meta(self, obj_key: str, entry, encoded_value: str, policy, now: str, *, is_new: bool) -> None:
    from .ttl import meta_ttl_value
    from .meta import apply_lifecycle

    r = self._redis()
    mapping = {
        "type": entry.gpdc_type,
        "codec": "native",
        "value": encoded_value,
        "cache_mode": policy.cache_mode,
        "ttl": meta_ttl_value(policy),
        "updated_at": now,
    }
    if is_new:
        mapping["created_at"] = now
    r.hset(obj_key, mapping=mapping)
    apply_lifecycle(r, obj_key, "", "", False, policy)
```

- [ ] **Step 2: Modify `_write_container_meta` to use `apply_lifecycle`**

```python
def _write_container_meta(
    self,
    obj_key: str,
    entry,
    raw_key: str,
    policy,
    now: str,
    *,
    is_new: bool,
    item_count: int = 0,
) -> None:
    from .ttl import meta_ttl_value
    from .meta import apply_lifecycle
    from .keys import make_ref_key

    r = self._redis()
    mapping = {
        "type": entry.gpdc_type,
        "codec": "native",
        "value": raw_key,
        "cache_mode": policy.cache_mode,
        "ttl": meta_ttl_value(policy),
        "updated_at": now,
        "item_count": str(item_count),
    }
    if is_new:
        mapping["created_at"] = now
    r.hset(obj_key, mapping=mapping)

    name = obj_key.split(":obj:")[1] if ":obj:" in obj_key else ""
    ref_key = make_ref_key(self._domain.name, self._name, name)
    apply_lifecycle(r, obj_key, raw_key, ref_key, True, policy)
```

- [ ] **Step 3: Run all tests**

Run: `./venv/bin/python -m pytest -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add src/gpdatacached/namespace.py
git commit -m "refactor: namespace write methods use unified apply_lifecycle"
```

---

### Task 8: Update _base.py setters for inherited override + unified lifecycle

**Files:**
- Modify: `src/gpdatacached/_base.py`

- [ ] **Step 1: Update `cache_mode` setter**

When setting `cache_mode` on an inherited object, it should transition out of inherited mode. Use `apply_lifecycle`:

```python
@cache_mode.setter
def cache_mode(self, mode: str) -> None:
    from .ttl import policy_from_meta, policy_for_cache_mode_change
    from .meta import apply_lifecycle
    from .keys import make_raw_key, make_ref_key

    if mode not in ("permanent", "ttl", "sliding"):
        raise ValueError(f"Invalid cache_mode: {mode!r}")
    key = self._canonical_redis_key()
    r = self._redis()
    current = policy_from_meta(r.hgetall(key))
    r.hset(key, "cache_mode", mode)
    new_policy = policy_for_cache_mode_change(current, mode)
    is_collection = r.hexists(key, "item_count")
    raw_key = r.hget(key, "value") if is_collection else ""
    ref_key = make_ref_key(self.domain.name, self.namespace.name, self._canonical_name)
    apply_lifecycle(r, key, raw_key, ref_key if is_collection else "", is_collection, new_policy)
```

- [ ] **Step 2: Update `ttl` setter**

```python
@ttl.setter
def ttl(self, seconds: int) -> None:
    from .ttl import policy_from_meta, policy_for_ttl_change
    from .meta import apply_lifecycle
    from .keys import make_ref_key

    key = self._canonical_redis_key()
    r = self._redis()
    current = policy_from_meta(r.hgetall(key))
    r.hset(key, "ttl", str(seconds))
    new_policy = policy_for_ttl_change(current, seconds)
    is_collection = r.hexists(key, "item_count")
    raw_key = r.hget(key, "value") if is_collection else ""
    ref_key = make_ref_key(self.domain.name, self.namespace.name, self._canonical_name)
    apply_lifecycle(r, key, raw_key, ref_key if is_collection else "", is_collection, new_policy)
```

- [ ] **Step 3: Update `persist()`**

```python
def persist(self) -> None:
    from .ttl import policy_from_meta, TTLPolicy
    from .meta import apply_lifecycle
    from .keys import make_ref_key

    key = self._canonical_redis_key()
    r = self._redis()
    current = policy_from_meta(r.hgetall(key))
    is_collection = r.hexists(key, "item_count")
    raw_key = r.hget(key, "value") if is_collection else ""
    ref_key = make_ref_key(self.domain.name, self.namespace.name, self._canonical_name)
    apply_lifecycle(
        r,
        key,
        raw_key,
        ref_key if is_collection else "",
        is_collection,
        TTLPolicy(
            cache_mode=current.cache_mode,
            declared_ttl=current.declared_ttl,
            expire_seconds=None,
        ),
    )
```

- [ ] **Step 4: Update `expire_in()` and `expire_at()`**

These should also apply to raw/ref for containers:

```python
def expire_in(self, seconds: int) -> None:
    from .meta import apply_lifecycle
    from .ttl import TTLPolicy
    from .keys import make_ref_key

    key = self._canonical_redis_key()
    r = self._redis()
    is_collection = r.hexists(key, "item_count")
    raw_key = r.hget(key, "value") if is_collection else ""
    ref_key = make_ref_key(self.domain.name, self.namespace.name, self._canonical_name)
    policy = TTLPolicy(cache_mode="ttl", declared_ttl=seconds, expire_seconds=seconds)
    apply_lifecycle(r, key, raw_key, ref_key if is_collection else "", is_collection, policy)

def expire_at(self, when: datetime.datetime) -> None:
    import time
    from .meta import apply_lifecycle
    from .ttl import TTLPolicy
    from .keys import make_ref_key

    key = self._canonical_redis_key()
    r = self._redis()
    seconds = max(0, int((when - datetime.datetime.now(datetime.UTC)).total_seconds()))
    is_collection = r.hexists(key, "item_count")
    raw_key = r.hget(key, "value") if is_collection else ""
    ref_key = make_ref_key(self.domain.name, self.namespace.name, self._canonical_name)
    policy = TTLPolicy(cache_mode="ttl", declared_ttl=seconds, expire_seconds=seconds)
    apply_lifecycle(r, key, raw_key, ref_key if is_collection else "", is_collection, policy)
```

- [ ] **Step 5: Run all tests**

Run: `./venv/bin/python -m pytest -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/gpdatacached/_base.py
git commit -m "feat: base setters use unified lifecycle, handle inherited override"
```

---

### Task 9: Update container.py _touch() to propagate lifecycle

**Files:**
- Modify: `src/gpdatacached/container.py`

- [ ] **Step 1: Update `_touch()` to use unified lifecycle and propagate**

```python
def _touch(self) -> None:
    from .ttl import policy_from_meta, policy_for_sliding_touch
    from .meta import apply_lifecycle, propagate_lifecycle

    key = self._canonical_redis_key()
    now = datetime.datetime.now(datetime.UTC).isoformat()
    r = self._redis()
    item_count = self._current_item_count()
    r.hset(key, mapping={"updated_at": now, "item_count": str(item_count)})
    current = policy_from_meta(r.hgetall(key))
    new_policy = policy_for_sliding_touch(current)
    if new_policy is not current:
        apply_lifecycle(r, key, self.raw_key, self.ref_key, True, new_policy)
        propagate_lifecycle(r, self.domain.name, self.namespace.name, self._binding_owner_name(), set())
```

- [ ] **Step 2: Update scalar.py value setter to use apply_lifecycle**

```python
@value.setter
def value(self, new_value: object) -> None:
    from .types import GPDCTypeRegistry
    from .ttl import policy_from_meta, policy_for_sliding_touch
    from .meta import apply_lifecycle
    from .keys import make_ref_key

    entry = GPDCTypeRegistry.lookup_by_gpdc_type(self.TYPE)
    expected_python_type = entry.python_type
    if not isinstance(new_value, expected_python_type):
        raise TypeError(f"Cannot assign {type(new_value).__name__!r} to {self.TYPE}")
    encoded = entry.codec_class.encode_value(new_value)
    key = self._canonical_redis_key()
    now = datetime.datetime.now(datetime.UTC).isoformat()
    r = self._redis()
    r.hset(key, mapping={"value": encoded, "updated_at": now})
    current = policy_from_meta(r.hgetall(key))
    new_policy = policy_for_sliding_touch(current)
    if new_policy is not current:
        apply_lifecycle(r, key, "", "", False, new_policy)
```

- [ ] **Step 3: Run all tests**

Run: `./venv/bin/python -m pytest -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add src/gpdatacached/container.py src/gpdatacached/scalar.py
git commit -m "feat: container touch and scalar setter use unified lifecycle with propagation"
```

---

### Task 10: Integration tests for inherited lifecycle behavior

**Files:**
- Modify: `tests/test_inherited_lifecycle.py`

- [ ] **Step 1: Add comprehensive integration tests**

These tests verify the acceptance criteria from the design spec:

```python
class TestSlidingNestedContainer:
    def test_sliding_parent_refreshes_nested_anon_ttl(self, r):
        config = GPDCDomainConfig(
            redis_host=REDIS_HOST, redis_port=REDIS_PORT, redis_db=REDIS_DB,
            redis_password=REDIS_PASSWORD,
            default_cache_mode="sliding", default_ttl_seconds=10, gc_enabled=False,
        )
        domain = GPDCDomain(DOMAIN + "_slide", config)
        ns = domain.get_namespace(NS)

        ns["d"] = {"a": [1, 2, 3]}
        d = ns["d"]
        anon_name = d["a"].name
        anon_obj_key = make_object_key(DOMAIN + "_slide", NS, anon_name)
        anon_raw_key = make_raw_key(DOMAIN + "_slide", NS, anon_name)

        initial_ttl = r.ttl(anon_raw_key)
        assert initial_ttl > 0

        import time
        time.sleep(1)

        d["b"] = "hello"

        refreshed_ttl = r.ttl(anon_raw_key)
        assert refreshed_ttl >= initial_ttl - 1

        pattern = f"gpdc:{DOMAIN + '_slide'}:{NS}:*"
        cursor = 0
        while True:
            cursor, keys = r.scan(cursor=cursor, match=pattern, count=100)
            if keys:
                r.delete(*keys)
            if cursor == 0:
                break


class TestTTLExpiryConsistency:
    def test_ttl_container_raw_key_has_ttl(self, r):
        config = GPDCDomainConfig(
            redis_host=REDIS_HOST, redis_port=REDIS_PORT, redis_db=REDIS_DB,
            redis_password=REDIS_PASSWORD,
            default_cache_mode="ttl", default_ttl_seconds=300, gc_enabled=False,
        )
        domain = GPDCDomain(DOMAIN + "_ttl2", config)
        ns = domain.get_namespace(NS)

        ns["lst"] = [1, 2, 3]
        obj_key = make_object_key(DOMAIN + "_ttl2", NS, "lst")
        raw_key = make_raw_key(DOMAIN + "_ttl2", NS, "lst")
        ref_key = make_ref_key(DOMAIN + "_ttl2", NS, "lst")

        assert r.ttl(obj_key) > 0
        assert r.ttl(raw_key) > 0
        assert r.ttl(ref_key) > 0

        pattern = f"gpdc:{DOMAIN + '_ttl2'}:{NS}:*"
        cursor = 0
        while True:
            cursor, keys = r.scan(cursor=cursor, match=pattern, count=100)
            if keys:
                r.delete(*keys)
            if cursor == 0:
                break


class TestInheritedOverride:
    def test_set_cache_mode_detaches_from_inherited(self, ns, r):
        ns["d"] = {"a": [1, 2, 3]}
        d = ns["d"]
        inner = d["a"]

        inner.cache_mode = "ttl"
        assert inner.cache_mode == "ttl"

        anon_obj_key = make_object_key(DOMAIN, NS, inner.canonical_name)
        assert r.hget(anon_obj_key, "cache_mode") == "ttl"

    def test_set_ttl_detaches_from_inherited(self, ns, r):
        ns["d"] = {"a": [1, 2, 3]}
        d = ns["d"]
        inner = d["a"]

        inner.ttl = 600
        anon_obj_key = make_object_key(DOMAIN, NS, inner.canonical_name)
        assert r.hget(anon_obj_key, "cache_mode") != "inherited" or r.hget(anon_obj_key, "ttl") == "600"
```

- [ ] **Step 2: Run all tests**

Run: `./venv/bin/python -m pytest tests/test_inherited_lifecycle.py -v`
Expected: All PASS

- [ ] **Step 3: Run full test suite**

Run: `./venv/bin/python -m pytest -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_inherited_lifecycle.py
git commit -m "test: add integration tests for inherited lifecycle behavior"
```

---

### Task 11: Final verification and lint

- [ ] **Step 1: Run full test suite**

Run: `./venv/bin/python -m pytest -v`
Expected: All PASS

- [ ] **Step 2: Run ruff lint and format**

Run: `./venv/bin/python -m ruff check src tests && ./venv/bin/python -m ruff format src tests`
Expected: No errors

- [ ] **Step 3: Fix any lint issues if needed**

- [ ] **Step 4: Final commit if lint fixes needed**

```bash
git add -A
git commit -m "chore: lint fixes for inherited lifecycle implementation"
```

---

## Spec Coverage Check

| Spec requirement | Task |
|-----------------|------|
| ObjectMeta pydantic model | Task 1 |
| cache_mode includes "inherited" | Task 2 |
| Anonymous objects use "inherited" | Task 6 |
| Effective policy computation | Task 4 |
| Unified lifecycle enforcement (obj/raw/ref) | Task 3 |
| Parent-to-child propagation | Task 5 |
| Override behavior (detach from inherited) | Task 8 |
| Container _touch triggers propagation | Task 9 |
| Namespace write uses unified lifecycle | Task 7 |
| Integration tests for acceptance criteria | Task 10 |
| All existing tests pass | Task 11 |
