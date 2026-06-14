# Refset Extraction + Set→Hash Migration Design

## Goal

Extract all reference set (ref set) maintenance into a dedicated `refset.py` module and migrate the underlying data structure from Redis Set to Redis Hash with reference counting. This eliminates the O(n) `_raw_still_contains` scan in container element operations and creates a single seam for future ref tracking changes.

## Background

### Current State

Ref sets track which objects reference a given container's raw data. They are Redis Sets keyed by `make_ref_key(domain, namespace, name)` → `gpdc:{domain}:{namespace}:ref:{name}`.

When a container slot holding a `ref:` pointer is overwritten or deleted, the system must determine whether the same ref value still appears elsewhere in the container's raw data before removing the container from the child's ref set. This check (`_raw_still_contains`) performs an O(n) `HVALS` or `LRANGE` + Python `in` check on every ref-slot mutation.

Approximately 30 raw Redis Set operations (`sadd`, `srem`, `smembers`) on ref keys are scattered across 4 source files: `container.py`, `namespace.py`, `types.py`, `gc.py`.

### Problem

1. **O(n) scan on cold path**: `_raw_still_contains` pulls all values from the container's raw Redis data to check if a single ref string is still present. For large containers (1000+ elements), this is expensive.

2. **No multiplicity tracking**: Redis Sets are binary (present/absent). If a child is referenced in 2 slots of the same parent, the ref set has one entry `{"parent"}`. Removing one slot requires scanning all raw data to confirm the other slot still references the child.

3. **Scattered operations**: ~30 raw Redis Set operations across 4 files with no centralized API. Future data structure changes would require touching all 4 files.

## Design

### New Module: `src/gpdatacached/refset.py`

Five functions encapsulating all ref set content operations. Uses Redis Hash (field = referrer name, value = reference count).

```python
def add_referrer(r, ref_key: str, referrer_name: str) -> None:
    """Register a reference from referrer_name (increment count)."""
    r.hincrby(ref_key, referrer_name, 1)

def release_ref(r, ref_key: str, referrer_name: str) -> bool:
    """Release one reference (decrement count). Returns True if fully removed."""
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

**Why `ref_key` parameter (not `domain/namespace/name`):**
- Most callers already hold a `ref_key` string (stored as attribute or local variable).
- `gc.py` SCAN loop produces raw ref keys, doesn't hold a `GPDCNamespace` object.
- `make_ref_key` stays in `keys.py`; `refset.py` only owns "what operation to perform on a ref_key."

**Two removal operations rationale:**
- `release_ref` (decrement): for slot-level removal where the container continues to exist. If count > 0 after decrement, the child is still referenced by another slot — skip GC.
- `remove_referrer` (hard `hdel`): for container teardown where ALL references from this container must be removed regardless of count. Used by `_teardown_owner_container`, `_cascade_delete_anonymous`, and GC cleanup.

### Changes to `container.py`

**DELETE** `_raw_still_contains` method — no longer needed; count tracking replaces it.

**DELETE** `_remove_self_from_refset` method — replaced by `release_ref` call inline.

**SIMPLIFY** `_release_element_ref`:

```python
# Before (3 steps + O(n) scan):
def _release_element_ref(self, old_encoded):
    if not isinstance(old_encoded, str) or not old_encoded.startswith("ref:"):
        return
    if self._raw_still_contains(old_encoded):      # O(n) scan
        return
    target_name = parse_ref_encoded(old_encoded)
    if target_name is None:
        return
    self._remove_self_from_refset(target_name)
    self._gc_anonymous_if_orphaned(target_name)

# After (2 steps + O(1)):
def _release_element_ref(self, old_encoded):
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

**UPDATE** `referrers()` → use `get_referrers`.
**UPDATE** `_gc_anonymous_if_orphaned` → use `has_referrers`.

### Changes to `namespace.py`

| Function | Current | New |
|---|---|---|
| `_setitem_container_ref` | `r.sadd(ref_key, key)` | `add_referrer(r, ref_key, key)` |
| `_cleanup_alias_binding` | `r.srem(ref_key, key)` | `release_ref(r, ref_key, key)` |
| `__delitem__` (alias branch) | `r.srem(ref_key, key)` | `release_ref(r, ref_key, key)` |
| `_teardown_owner_container` | `r.srem(nested_ref_key, key)` | `remove_referrer(r, nested_ref_key, key)` |
| `_cascade_delete_anonymous` | `r.srem(nested_ref_key, anon_name)` | `remove_referrer(r, nested_ref_key, anon_name)` |
| `_cleanup_existing_for_overwrite` | `r.smembers(ref_key)` | `get_referrers(r, ref_key)` |
| `__setitem__` | `r.smembers(ref_key)` | `get_referrers(r, ref_key)` |
| `__delitem__` (owner branch) | `r.smembers(ref_key)` | `get_referrers(r, ref_key)` |
| `_cleanup_alias_binding` (truthiness) | `not r.smembers(ref_key)` | `not has_referrers(r, ref_key)` |
| `__delitem__` (truthiness) | `not r.smembers(ref_key)` | `not has_referrers(r, ref_key)` |
| `_cascade_delete_anonymous` | `r.smembers(ref_key)` | `get_referrers(r, ref_key)` |

**NOT migrated:** `r.delete(ref_key)` ×2 (batched with raw_key/obj_key deletion).

### Changes to `types.py`

| Function | Current | New |
|---|---|---|
| `_ref_existing_container` | `namespace._redis().sadd(ref_key, parent_name)` | `add_referrer(namespace._redis(), ref_key, parent_name)` |
| `_create_anonymous_object` | `r.sadd(ref_key, parent_name)` | `add_referrer(r, ref_key, parent_name)` |

### Changes to `gc.py`

| Function | Current | New |
|---|---|---|
| `_gc_scan_raw_keys` ×2 | `r.smembers(ref_key)` | `get_referrers(r, ref_key)` |
| `_gc_scan_ref_keys` | `r.smembers(ref_key)` | `get_referrers(r, ref_key)` |
| `_gc_scan_ref_keys` ×6 | `r.srem(ref_key, member)` | `remove_referrer(r, ref_key, member)` |

**NOT migrated:** SCAN loop (bulk key iteration), `r.delete(ref_key)` ×3 (batched deletion).

### Changes to tests

All test files that directly inspect ref sets via raw Redis operations (~30+ sites):

- `r.smembers(ref_key)` → `set(r.hkeys(ref_key))` or `get_referrers(r, ref_key)`
- `r.sadd(ref_key, name)` → `r.hset(ref_key, name, "1")` or `add_referrer(r, ref_key, name)`
- `r.srem(ref_key, name)` → `r.hdel(ref_key, name)` or `remove_referrer(r, ref_key, name)`
- `assert r.smembers(ref_key) == {"a"}` → `assert set(r.hkeys(ref_key)) == {"a"}`

### What stays unchanged

- `make_ref_key`, `make_ref_key_prefix` in `keys.py` — key construction is shared infrastructure
- `collect_ref_names_from_raw` in `keys.py` — raw data scanning utility
- `r.delete(ref_key)` in teardown/GC — batched deletion, not a set-content operation
- `apply_lifecycle` TTL on ref keys — lifecycle concern, not ref tracking
- `namespace.py:clear()` — bulk pattern-based delete

## Correctness Analysis

### Multi-reference scenario

```python
ns["parent"] = {"x": ns["child"], "y": ns["child"]}
# ref:child Hash = {"parent": "2"}  (two slots reference child)

del ns["parent"]["x"]
# release_ref(ref:child, "parent") → count 2→1, returns False
# GC skipped — child still referenced by "y"

del ns["parent"]["y"]
# release_ref(ref:child, "parent") → count 1→0, hdel, returns True
# GC triggered — child is orphaned
```

### Container teardown

```python
del ns["parent"]
# _teardown_owner_container scans raw data, finds child refs
# remove_referrer(ref:child, "parent") → hdel entirely (regardless of count)
# Correct — container is being destroyed, all references gone
```

### Namespace alias

```python
ns["alias"] = ns["owner"]
# add_referrer(ref:owner, "alias") → count = 1

del ns["alias"]
# release_ref(ref:owner, "alias") → count 1→0, hdel, returns True
# Correct — alias is single-reference
```

### Count consistency

Counts are maintained by the same code paths that manage raw data slots:
- `encode_element` (adding a slot) → `add_referrer`
- `_release_element_ref` (removing a slot) → `release_ref`
- Container teardown → `remove_referrer`

The GC provides a safety net: it validates referrers against raw data and removes stale entries.

## Performance Impact

| Operation | Before (Set) | After (Hash) |
|---|---|---|
| Add referrer | O(1) `sadd` | O(1) `hincrby` |
| Remove one ref | O(n) `_raw_still_contains` + O(1) `srem` | O(1) `hincrby` + conditional `hdel` |
| Remove all refs | O(1) `srem` | O(1) `hdel` |
| Check has referrers | O(n) `smembers` (was not using `scard`) | O(1) `hlen > 0` |
| List referrers | O(n) `smembers` | O(n) `hkeys` |
| GC scan | O(n) `smembers` per ref key | O(n) `hkeys` per ref key |

Main win: slot-level ref removal goes from O(n) to O(1) for containers with ref-valued elements.
