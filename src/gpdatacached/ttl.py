# src/gpdatacached/ttl.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, NamedTuple

from .keys import collect_ref_names_from_raw, make_object_key, make_ref_key
from .meta import ObjectMeta

if TYPE_CHECKING:
    import redis


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


def policy_from_defaults(cache_mode: str, ttl: int) -> TTLPolicy:
    if cache_mode not in ("permanent", "ttl", "sliding"):
        raise ValueError(f"Invalid cache_mode: {cache_mode!r}")
    expire_seconds = ttl if cache_mode in ("ttl", "sliding") and ttl > 0 else None
    return TTLPolicy(cache_mode=cache_mode, declared_ttl=ttl, expire_seconds=expire_seconds)


def policy_from_meta(meta: ObjectMeta) -> TTLPolicy:
    if meta.cache_mode in ("ttl", "sliding") and meta.ttl > 0:
        return TTLPolicy(cache_mode=meta.cache_mode, declared_ttl=meta.ttl, expire_seconds=meta.ttl)
    return TTLPolicy(cache_mode=meta.cache_mode, declared_ttl=meta.ttl, expire_seconds=None)


def policy_for_cache_mode_change(current: TTLPolicy, new_mode: str) -> TTLPolicy:
    if new_mode not in ("permanent", "ttl", "sliding"):
        raise ValueError(f"Invalid cache_mode: {new_mode!r}")
    if new_mode == "permanent":
        return TTLPolicy(cache_mode=new_mode, declared_ttl=current.declared_ttl, expire_seconds=None)
    if current.declared_ttl > 0:
        return TTLPolicy(cache_mode=new_mode, declared_ttl=current.declared_ttl, expire_seconds=current.declared_ttl)
    return TTLPolicy(cache_mode=new_mode, declared_ttl=current.declared_ttl, expire_seconds=None)


def policy_for_ttl_change(current: TTLPolicy, new_ttl: int) -> TTLPolicy:
    if current.cache_mode == "permanent":
        return TTLPolicy(cache_mode=current.cache_mode, declared_ttl=new_ttl, expire_seconds=None)
    if new_ttl > 0:
        return TTLPolicy(cache_mode=current.cache_mode, declared_ttl=new_ttl, expire_seconds=new_ttl)
    return TTLPolicy(cache_mode=current.cache_mode, declared_ttl=new_ttl, expire_seconds=None)


def policy_for_sliding_touch(current: TTLPolicy) -> TTLPolicy:
    if current.cache_mode == "sliding" and current.declared_ttl > 0:
        return TTLPolicy(
            cache_mode=current.cache_mode,
            declared_ttl=current.declared_ttl,
            expire_seconds=current.declared_ttl,
        )
    return current


def apply_policy(r: "redis.Redis", obj_key: str, policy: TTLPolicy) -> None:
    if policy.expire_seconds is None:
        r.persist(obj_key)
    else:
        r.expire(obj_key, policy.expire_seconds)


def apply_lifecycle(r, obj_key: str, raw_key: str, ref_key: str, is_collection: bool, policy) -> None:
    apply_policy(r, obj_key, policy)
    if is_collection:
        if raw_key:
            apply_policy(r, raw_key, policy)
        if ref_key:
            apply_policy(r, ref_key, policy)


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
            apply_lifecycle(pipe, child_obj_key, child_raw_key, child_ref_key, child_meta.is_collection(), policy)
        pipe.execute()

    for child_name, _, _ in updates:
        propagate_lifecycle(r, domain, namespace, child_name, policy, visited)
