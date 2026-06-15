"""Data-object lifecycle management: cache-mode/TTL policy, Redis TTL application, and propagation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import NamedTuple

from .keys import collect_ref_names_from_raw, make_object_key, make_ref_key
from .meta import ObjectMeta


class _LifecycleContext(NamedTuple):
    r: object
    key: str
    meta: ObjectMeta
    raw_key: str
    ref_key: str
    is_collection: bool


@dataclass(frozen=True)
class TTLPolicy:
    cache_mode: str
    declared_ttl: int
    expire_seconds: int | None


def _validate_policy(policy: TTLPolicy) -> None:
    if policy.cache_mode not in ("permanent", "ttl", "sliding"):
        raise ValueError(f"Invalid cache_mode: {policy.cache_mode!r}")
    if policy.declared_ttl < 0:
        raise ValueError(f"ttl must be >= 0: {policy.declared_ttl}")
    if policy.cache_mode in ("ttl", "sliding") and policy.declared_ttl <= 0:
        raise ValueError(f"cache_mode='{policy.cache_mode}' requires declared_ttl > 0, got {policy.declared_ttl}")


def policy_from_defaults(cache_mode: str, ttl: int) -> TTLPolicy:
    if cache_mode not in ("permanent", "ttl", "sliding"):
        raise ValueError(f"Invalid cache_mode: {cache_mode!r}")
    if ttl < 0:
        raise ValueError(f"ttl must be >= 0: {ttl}")
    if cache_mode in ("ttl", "sliding") and ttl <= 0:
        raise ValueError(f"cache_mode='{cache_mode}' requires ttl > 0, got {ttl}")
    expire_seconds = ttl if cache_mode in ("ttl", "sliding") and ttl > 0 else None
    return TTLPolicy(cache_mode=cache_mode, declared_ttl=ttl, expire_seconds=expire_seconds)


def policy_from_meta(meta: ObjectMeta) -> TTLPolicy:
    if meta.cache_mode in ("ttl", "sliding") and meta.ttl > 0:
        return TTLPolicy(cache_mode=meta.cache_mode, declared_ttl=meta.ttl, expire_seconds=meta.ttl)
    return TTLPolicy(cache_mode=meta.cache_mode, declared_ttl=meta.ttl, expire_seconds=None)


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


def propagate_lifecycle(r, domain: str, namespace: str, parent_name: str, policy: TTLPolicy, visited: set[str]) -> None:
    parent_obj_key = make_object_key(domain, namespace, parent_name)
    parent_meta = ObjectMeta.load(r, parent_obj_key)
    if parent_meta is None or not parent_meta.is_collection():
        return

    child_names = collect_ref_names_from_raw(r, parent_meta.value, parent_meta.type)

    updates = []
    for child_name in child_names:
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
        now = datetime.now(UTC)
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
        propagate_lifecycle(r, domain, namespace, child_name, policy, visited)


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
