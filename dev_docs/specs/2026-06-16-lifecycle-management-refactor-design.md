# Lifecycle Management Refactor Design

## Goal

Refactor the data-object lifecycle management module (currently `ttl.py`) to fix a silent correctness defect in sliding-window TTL refresh, centralize duplicated validation, improve naming/cohesion, and rename the module to `lifecycle.py` so its name matches its responsibility.

This design implements the approved findings of `dev_docs/reviews/lifecycle-management-refactor-review.md` (F1–F9).

## Background

### Current State

`src/gpdatacached/ttl.py` (125 lines) defines `TTLPolicy` plus 7 functions/classes managing cache-mode/TTL policy derivation, Redis TTL application, and policy propagation across owned-object subtrees. It is imported by `objects.py`, `container.py`, `scalar.py`, `types.py`, `namespace.py`, `builtins/list_object.py`, `builtins/dict_object.py`, `extensions/pydantic_support.py`, and 3 test files. `__init__.py` does **not** re-export any of its symbols.

### Problems (from the approved review)

1. **F1 (critical, correctness)** — Sliding TTL refresh is a no-op. `policy_from_meta` always sets `expire_seconds = declared_ttl`, so `set_lifecycle`'s change-detection (`current.expire_seconds != policy.expire_seconds`) is always False for sliding; `r.expire` is never re-applied on touch. Verified empirically: a `sliding, ttl=10` scalar stays at TTL 9 after a touch instead of resetting to 10. The existing test only asserts `ttl > 0`, so the regression went undetected.
2. **F3 (DRY)** — `cache_mode`/`ttl` validation is duplicated in 6 places: `_validate_policy`, `policy_from_defaults`, `objects.update_cache_mode_and_ttl`, and the two `config.py` classes (`GPDCNamespaceConfig`, `GPDCDomainConfig`).
3. **F4** — `expire_seconds` is a pure derivation of `(cache_mode, declared_ttl)` at every call site, yet exposed as a constructor field, forcing callers to recompute it and enabling the F1 confusion.
4. **F5** — `apply_policy` (one key) and `apply_lifecycle` (key trio) overlap; names don't convey behavior; `apply_lifecycle` lacks type annotations.
5. **F6** — `_LifecycleContext` is defined in `ttl.py` but used only by `objects.py`.
6. **F7** — `propagate_lifecycle` mixes traversal/batching/recursion; the cycle-guard `visited` set is leaked into the public signature.
7. **F8** — `set_lifecycle` validates after a Redis read; `_touch` double-saves meta.
8. **F9** — module named `ttl.py` but manages lifecycle broadly; magic string literals; leftover path-comment header.

## Design

### Approved decisions

- **Sliding refresh scope**: restore the original (broken) cascade intent — self-owned touched → refresh self + owned-children subtree downward; owned/anon touched → propagate up to the first self-owned ancestor, then refresh its subtree downward; scalar → refresh self only.
- **F1 mechanism**: `apply_ttl` + dedicated `propagate_ttl_refresh` (cleanest separation; `set_lifecycle` stays for real policy changes only).
- **Sub-decisions**: (a) validation as a `frozenset` constant + validator function, **not** an Enum; (b) **no** backward-compat shim for the rename; (c) refresh fires on **mutation only** (reads don't touch, matching current behavior); (d) `propagate_ttl_refresh` does **not** rewrite child meta (refresh focuses on Redis TTL; children `updated_at` left unchanged to minimize writes).

### 1. Module rename

- `src/gpdatacached/ttl.py` → **`src/gpdatacached/lifecycle.py`**; replace the `# src/gpdatacached/ttl.py` header with a module docstring.
- All imports updated (`from .ttl import` → `from .lifecycle import`, and `from gpdatacached.ttl import` in tests). No shim is kept; blast radius is internal + tests only.

### 2. Single source of truth for validation (F3)

Validation is split into **two tiers**. This is deliberate: `TTLPolicy` is a value object that must tolerate degenerate pairs read back from legacy/corrupt meta (e.g. `cache_mode="ttl", ttl=0` — covered by `test_ttl_meta_with_zero_ttl`), while user-facing creation entry points must enforce the strict "ttl/sliding requires ttl>0" rule.

In `lifecycle.py`:

```python
VALID_CACHE_MODES = frozenset({"permanent", "ttl", "sliding"})

def _validate_basic(cache_mode: str, ttl: int) -> None:
    """Invariant of a TTLPolicy value object: sound mode, non-negative ttl.
    Tolerates degenerate (ttl-mode, ttl=0) pairs read from legacy meta."""
    if cache_mode not in VALID_CACHE_MODES:
        raise ValueError(f"Invalid cache_mode: {cache_mode!r}")
    if ttl < 0:
        raise ValueError(f"ttl must be >= 0: {ttl}")

def validate_cache_mode_and_ttl(cache_mode: str, ttl: int) -> None:
    """Strict validation for user-facing policy CREATION."""
    _validate_basic(cache_mode, ttl)
    if cache_mode in ("ttl", "sliding") and ttl <= 0:
        raise ValueError(f"cache_mode={cache_mode!r} requires ttl > 0, got {ttl}")
```

- `config.py`'s `GPDCNamespaceConfig` and `GPDCDomainConfig` validators delegate to `validate_cache_mode_and_ttl` (strict — you cannot configure a ttl/sliding namespace with `ttl_seconds <= 0`). Import is non-circular: `lifecycle` imports only `keys` (trivial) and `meta` (pydantic), neither of which imports `config`.
- `TTLPolicy.__post_init__` calls **`_validate_basic`** only → construction guarantees a sound value object without rejecting legacy data.
- `_validate_policy` is deleted. The strict tests (`test_validate_policy_rejects_*`) migrate to `policy_from_defaults` (the strict creation entry point); a basic-construction test covers `__post_init__` (invalid mode / negative ttl raise).

### 3. `TTLPolicy`: 2 fields + derived property (F4)

```python
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
```

- `policy_from_defaults(cache_mode, ttl)` calls `validate_cache_mode_and_ttl` (strict) then constructs; `policy_from_meta(meta)` constructs directly (basic validation only — preserves legacy-data tolerance; the `expire_seconds` property yields `None` for a ttl/sliding meta whose `ttl==0`).
- **`policy_for_sliding_touch` is deleted** — with `expire_seconds` derived and the refresh moved to `propagate_ttl_refresh`, it has no remaining purpose.

### 4. Collapsed TTL application (F5)

```python
def apply_ttl(r, keys, policy: TTLPolicy) -> None:
    seconds = policy.expire_seconds
    for k in keys:
        if not k:
            continue
        if seconds is None:
            r.persist(k)
        else:
            r.expire(k, seconds)
```

`apply_policy` and `apply_lifecycle` are removed; callers pass an explicit key list (`[obj_key]` for scalars, `[obj_key, raw_key, ref_key]` for containers).

### 5. Fixed sliding refresh (F1)

Two new building blocks:

- **`propagate_ttl_refresh(r, domain, namespace, parent_name)`** — public wrapper around a private `_propagate_ttl_refresh(..., visited)` recursion. Walks owned children (`child.owner == parent_name`), cycle-guard internal (no leaked param; per-level child-name dedup plus the ownership-tree invariant make graph cycles impossible). For each child: load child meta, derive the **child's own** policy via `policy_from_meta`, and if it declares expiry (`expire_seconds is not None`) `apply_ttl` its keys (`obj_key`, plus `raw_key`/`ref_key` when it is a collection); then recurse regardless of the child's mode (so deeper owned descendants are still reached). **Does not rewrite child meta.** Using each child's own policy (rather than the parent's) is safer for mixed-policy subtrees — a permanent child is not accidentally given a TTL — and is equivalent in the common uniform-sliding-subtree case.
- **`GPDCDataObject.refresh_ttl()`** — helper:
  - load `_LifecycleContext`;
  - derive `policy = policy_from_meta(ctx.meta)`;
  - if `policy.expire_seconds is not None`: `apply_ttl(ctx.r, [ctx.key] + ([ctx.raw_key, ctx.ref_key] if ctx.is_collection else []), policy)`;
  - `propagate_ttl_refresh(ctx.r, domain, namespace, canonical_name)` (cascade to owned subtree).

Touch wiring (mutation only):

- **Scalar** (`scalar.py` value setter): after saving the updated value meta, if `policy.cache_mode == "sliding" and policy.declared_ttl > 0` → `self.refresh_ttl()`.
- **Container, self-owned** (`container._touch`, `meta.owner == canonical_name`): after the timestamp save, if sliding → `self.refresh_ttl()`.
- **Container, owned/anon** (`container._touch`, else branch): `self._propagate_sliding_ttl_upward(meta.owner)` walks up to the first self-owned ancestor and calls `ancestor.refresh_ttl()` (cascade down from there).

`_propagate_sliding_ttl_upward` is simplified to use `refresh_ttl` on the resolved ancestor (it no longer routes through `set_lifecycle`).

### 6. Policy-change path, cleaned (F7)

`set_lifecycle(policy, *, propagate=True)`:

```python
ctx = self._load_lifecycle_context()
current = policy_from_meta(ctx.meta)
changed = (current.cache_mode != policy.cache_mode
           or current.declared_ttl != policy.declared_ttl)
ctx.meta.with_updates(cache_mode=policy.cache_mode, ttl=policy.declared_ttl,
                      updated_at=now).save(ctx.r, ctx.key)
if changed:
    apply_ttl(ctx.r, [ctx.key, ctx.raw_key, ctx.ref_key], policy)  # empties skipped
if changed and propagate:
    propagate_lifecycle(ctx.r, domain, namespace, canonical_name, policy)
```

- No `_validate_policy` call (validation happened at construction) → F8's "validate before I/O" is moot.
- `_touch` no longer calls `set_lifecycle`, so the meta double-save (F8) disappears.

`propagate_lifecycle(r, domain, namespace, parent_name, policy)` → public wrapper + private `_propagate_lifecycle(..., visited)`:

- `visited` **removed from the public signature**; dedup happens via per-level `dict.fromkeys(child_names)` (the ownership-tree invariant means graph re-visits are impossible; `visited` only ever guarded duplicate ref names within a level).
- Still skips children whose policy already matches `policy` (it is a policy *change*).
- Rewrites each changed child's meta (`cache_mode`, `ttl`, `updated_at`) and calls `apply_ttl` on its keys; recurses; uses a pipeline for the meta writes exactly as today.

### 7. `_LifecycleContext` relocated (F6)

Moved into `objects.py` next to `_load_lifecycle_context`. `lifecycle.py` no longer defines or imports it.

### 8. Caller simplifications (F3 cont.)

- `objects.update_cache_mode_and_ttl` → `policy_from_defaults(cache_mode, ttl)` (drop inline validation + manual `expire_seconds`).
- `objects.expire_in` / `expire_at` / `persist` → 2-field `TTLPolicy(...)`.
- `namespace._write_scalar_meta`: `apply_lifecycle(r, obj_key, "", "", False, policy)` → `apply_ttl(r, [obj_key], policy)`.
- `namespace.__setitem__` container branch: `apply_lifecycle(r, obj_key, raw_key, ref_key, True, policy)` → `apply_ttl(r, [obj_key, raw_key, ref_key], policy)`.
- `types._create_anonymous_object`: build the key list conditionally on `entry.is_container` and call `apply_ttl`.

### 9. Tests (F2 + API updates)

- `tests/test_ttl.py` → **`tests/test_lifecycle.py`**; imports updated.
- `TTLPolicy(...)` constructions drop `expire_seconds`.
- `apply_policy`/`apply_lifecycle` unit tests → `apply_ttl`.
- `_validate_policy` tests split by tier: invalid-mode / negative-ttl cases → construction-time (`TTLPolicy(...)` raises via `__post_init__`); the "ttl/sliding requires >0" cases → `policy_from_defaults(...)` raises (strict entry point).
- `propagate_lifecycle(..., set())` calls → `propagate_lifecycle(...)` (no `visited`).
- `policy_for_sliding_touch` tests removed.
- **Strengthen sliding coverage (F2)**: assert a refresh actually resets TTL toward `declared_ttl` — e.g. `refreshed_ttl > remaining_before_touch` — plus add (a) a scalar sliding refresh end-to-end test, and (b) an owned/anon upward-propagation end-to-end test.
- `tests/test_meta.py` and `tests/test_objects.py`: update `apply_lifecycle` → `apply_ttl` and `TTLPolicy` constructions.

## Data flow

- **Policy change** (e.g. `update_cache_mode_and_ttl`, `persist`, `expire_in`): construct `TTLPolicy` → `set_lifecycle` → (if changed) `apply_ttl` self + `propagate_lifecycle` to owned children (meta rewrite + TTL).
- **Sliding refresh** (mutation of a sliding object): `_touch`/scalar-setter → `refresh_ttl` → `apply_ttl` self + `propagate_ttl_refresh` to owned children (TTL only, no meta rewrite); owned/anon first bubbles up to the self-owned ancestor.

## Error handling

Unchanged: invalid `cache_mode`/`ttl` raise `ValueError` (now at construction, earlier in the flow). `ObjectGoneError` from missing meta in `_load_lifecycle_context`/touch unchanged. `LifecyclePermissionError` paths (`update_cache_mode_and_ttl`, `expire_in/at`, `persist`) unchanged; refresh does not introduce new permission gates (it only re-applies existing TTL on objects already accessible to the caller).

## Testing strategy

TDD per the Mandatory Workflow Sequence: for each behavioral change (F1 refresh; F3/F4 validation + derived field; F5 apply_ttl; F7 propagate signature) write a failing test first, then implement. End-to-end sliding tests run against the test Redis (`127.0.0.1:6379` db=1, password `123456`). `ruff check`/`ruff format` and `pytest` must pass before completion.

## Out of scope

- Read-triggered sliding refresh (current behavior is mutate-only).
- Batched child-meta reads in propagation (F7 perf note).
- Enum migration for cache modes (constant chosen instead).
- Any change to `gc.py`, `refset.py`, `codec.py`, or the public `__init__.py` exports.

## Risk notes

- `config.py` importing `lifecycle.py` slightly widens the config import graph; verified non-circular (lifecycle → keys, meta; neither imports config). If a surprise cycle appears, the validator can be dropped into a tiny `constants.py` as fallback.
- Dropping `visited` from `propagate_lifecycle` relies on the ownership-tree invariant; the strengthened tests plus the existing `test_propagate_lifecycle_uses_owner_check` cover the duplicate-ref-name case.
