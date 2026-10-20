"""Data-object lifecycle management: cache-mode/TTL policy, Redis TTL application, and propagation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .keys import collect_ref_names_from_raw, make_object_key, make_ref_key
from .meta import ObjectMeta


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


def policy_from_meta(meta: ObjectMeta) -> TTLPolicy:
    return TTLPolicy(cache_mode=meta.cache_mode, declared_ttl=meta.ttl)


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


def propagate_lifecycle(r, domain: str, namespace: str, parent_name: str, policy: TTLPolicy) -> None:
    """Propagate a policy change to an owned subtree (meta rewrite + TTL apply)."""
    _propagate_lifecycle(r, domain, namespace, parent_name, policy, set())


def _propagate_lifecycle(
    r, domain: str, namespace: str, parent_name: str, policy: TTLPolicy, visited: set[str]
) -> None:
    parent_obj_key = make_object_key(domain, namespace, parent_name)
    parent_meta = ObjectMeta.load(r, parent_obj_key)
    if parent_meta is None or not parent_meta.is_collection():
        return

    child_names = collect_ref_names_from_raw(r, parent_meta.value, parent_meta.type)

    updates = []
    for child_name in dict.fromkeys(child_names):
        if child_name in visited:
            continue
        visited.add(child_name)

        child_obj_key = make_object_key(domain, namespace, child_name)
        child_meta = ObjectMeta.load(r, child_obj_key)
        if child_meta is None or child_meta.owner != parent_name:
            continue

        current = policy_from_meta(child_meta)
        if current.cache_mode == policy.cache_mode and current.expire_seconds == policy.expire_seconds:
            continue

        updates.append((child_name, child_obj_key, child_meta))

    if updates:
        pipe = r.pipeline(transaction=False)
        now = datetime.now(timezone.utc)
        for child_name, child_obj_key, child_meta in updates:
            updated = child_meta.with_updates(
                cache_mode=policy.cache_mode,
                ttl=policy.declared_ttl,
                updated_at=now,
            )
            updated.save(pipe, child_obj_key)

            child_raw_key = child_meta.value if child_meta.is_collection() else ""
            child_ref_key = make_ref_key(domain, namespace, child_name)
            keys = [child_obj_key]
            if child_meta.is_collection():
                keys.extend([child_raw_key, child_ref_key])
            apply_ttl(pipe, keys, policy)
        pipe.execute()

    for child_name, _, _ in updates:
        _propagate_lifecycle(r, domain, namespace, child_name, policy, visited)


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

    # Two-pass walk mirroring _propagate_lifecycle: first gather this level's
    # owned children and their TTL keys, then batch the apply_ttl calls into a
    # single pipeline (one round-trip per level instead of one per child).
    # Owned children with no expiry are skipped for TTL re-apply but still
    # recursed into so deeper descendants are reached.
    # (name, ttl_keys, policy): keys/policy are None for children that declare
    # no expiry — they are only recursed into, not re-applied.
    children: list[tuple[str, list[str] | None, TTLPolicy | None]] = []
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
            children.append((child_name, keys, child_policy))
        else:
            # No TTL to re-apply, but still recurse to reach owned descendants.
            children.append((child_name, None, None))

    if any(keys is not None for _, keys, _ in children):
        pipe = r.pipeline(transaction=False)
        for _, child_keys, child_pol in children:
            if child_keys is not None and child_pol is not None:
                apply_ttl(pipe, child_keys, child_pol)
        pipe.execute()

    for child_name, _, _ in children:
        _propagate_ttl_refresh(r, domain, namespace, child_name, visited)
