# P0-2: Inherited Lifecycle + P2-1: Lifecycle Convergence

## Status

Design approved — ready for implementation planning.

## Problem

TTL/sliding only applies to `obj:` keys. `raw:`, `ref:`, and anonymous nested containers are not treated as part of the same lifecycle unit. This causes:

1. Child anonymous containers can expire before their parent.
2. Reading nested containers raises `KeyError` when metadata is still present.
3. Lifecycle logic is scattered across `_base.py`, `container.py`, `scalar.py`, `types.py`, `namespace.py`, and `ttl.py`, making it easy for future fixes to miss paths.

## Approach

Follow `dev_docs/inherited_lifecycle_design.md` as the baseline. Introduce `cache_mode="inherited"` for anonymous objects, plus unified lifecycle enforcement that covers `obj:`, `raw:`, and `ref:`.

## Changes

### 1. New `ObjectMeta` pydantic model (new file: `src/gpdatacached/meta.py`)

Fields:

- `type: str`
- `codec: str = "native"`
- `value: str = ""`
- `cache_mode: Literal["permanent", "ttl", "sliding", "inherited"]`
- `ttl: int = 0`
- `created_at: datetime | None = None`
- `updated_at: datetime | None = None`
- `item_count: int | None = None`

Methods:

- `from_redis(data: dict) -> ObjectMeta` — parse Redis hash dict
- `to_redis_mapping() -> dict[str, str]` — convert to Redis hset mapping
- `is_collection() -> bool`
- `is_inherited() -> bool`
- `declares_expiry() -> bool`

Validation:

- `cache_mode` must be one of the four allowed values
- `ttl >= 0`

### 2. Expand `cache_mode` to include `"inherited"`

Update `ttl.py` functions to accept `"inherited"` as valid in `policy_from_meta()`. Add validation in `policy_from_defaults()` and `policy_for_cache_mode_change()` that `"inherited"` is not accepted for top-level objects (only for anonymous ones).

### 3. Anonymous object creation uses `cache_mode="inherited"`

In `types.py::_create_anonymous_object()`: write `cache_mode="inherited"` instead of copying parent's `cache_mode`. The anonymous object's effective TTL is computed from its parents at runtime, not stored statically.

### 4. Effective policy computation (new functions in `ttl.py` or `meta.py`)

- `effective_policy(meta: ObjectMeta, r: redis.Redis, domain: str, namespace: str) -> TTLPolicy` — for non-inherited objects, returns policy from meta; for inherited objects, reads ref set, loads parent policies, and aggregates.
- Aggregation rule: if any parent is `permanent` → `permanent`; otherwise take max `remaining_ttl()` among parents, preferring `sliding` on tie.

### 5. Unified lifecycle enforcement

A single function `apply_lifecycle(r, domain, namespace, name, meta, policy)` that:

1. Applies `expire/persist` to `obj:` key
2. If `meta.is_collection()`: applies same to `raw:` key (if it exists)
3. Applies same to `ref:` key

This replaces all scattered `apply_policy()` calls in `_base.py`, `container.py`, `scalar.py`, `namespace.py`, and `types.py`.

### 6. Parent-to-child propagation

When a parent object's lifecycle changes (`_touch()`, `cache_mode` setter, `ttl` setter, `persist()`, `expire_in()`, `expire_at()`):

1. Collect direct anonymous children from ref set or raw data.
2. For each child still in `inherited` mode, recompute `effective_policy()`.
3. Apply the new effective policy via unified enforcement.
4. Recurse if children themselves have anonymous descendants in `inherited` mode.

### 7. Override behavior

When a user explicitly calls `cache_mode=`, `ttl=`, `persist()`, `expire_in()`, or `expire_at()` on an `inherited` object:

- The object transitions out of `inherited` to the specified mode.
- Subsequent parent propagation skips this object.

Implementation: in `_base.py` setters, if current `cache_mode == "inherited"`, set the new mode explicitly and update meta.

## Files modified

| File | Change |
|------|--------|
| `src/gpdatacached/meta.py` | **New** — ObjectMeta pydantic model |
| `src/gpdatacached/ttl.py` | Add effective policy computation; accept "inherited"; update apply functions |
| `src/gpdatacached/types.py` | Anonymous object creation uses "inherited"; lifecycle calls go through unified path |
| `src/gpdatacached/_base.py` | cache_mode/ttl setters handle "inherited" override; use ObjectMeta; unified apply |
| `src/gpdatacached/container.py` | `_touch()` triggers propagation; use ObjectMeta |
| `src/gpdatacached/scalar.py` | Use ObjectMeta for value setter |
| `src/gpdatacached/namespace.py` | `_write_scalar_meta` / `_write_container_meta` use ObjectMeta and unified lifecycle |
| `tests/` | New tests for inherited lifecycle, effective policy, propagation, override |

## Not in scope

- Empty container GC fix (P0-1, already done)
- Stale alias semantics (P0-3)
- Negative TTL runtime validation (P1-2)
- Scalar missing payload error (P1-3)
- Test fixture GC cleanup (P1-1)

## Acceptance criteria

- TTL container: after real expiry, all related keys (`obj:`, `raw:`, `ref:`) are in consistent state.
- Sliding container: after multiple writes, nested anonymous containers remain readable.
- No `KeyError` from child anonymous container early expiry.
- `cache_mode` on anonymous object reads as `"inherited"` by default.
- Explicit `cache_mode`/`ttl`/`persist()`/`expire_in()`/`expire_at()` on inherited object detaches it from parent lifecycle.
- Multi-parent anonymous object uses longer lifetime.
- All existing tests continue to pass.
