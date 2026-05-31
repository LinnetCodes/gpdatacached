# src/gpdatacached/ttl.py
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .keys import collect_ref_names_from_raw, make_object_key, make_ref_key
from .meta import ObjectMeta

if TYPE_CHECKING:
    import redis


@dataclass(frozen=True)
class TTLPolicy:
    cache_mode: str
    declared_ttl: int
    expire_seconds: int | None


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


def effective_policy(meta: ObjectMeta, r, domain: str, namespace: str, obj_name: str):
    if not meta.is_inherited():
        return policy_from_meta(meta)

    ref_key = make_ref_key(domain, namespace, obj_name)
    obj_prefix = f"gpdc:{domain}:{namespace}:obj:"
    referrers = r.smembers(ref_key)

    for ref_name in referrers:
        parent_obj_key = obj_prefix + ref_name
        parent_meta = ObjectMeta.load(r, parent_obj_key)
        if parent_meta is None:
            continue
        if parent_meta.cache_mode == "permanent":
            return TTLPolicy(cache_mode="permanent", declared_ttl=0, expire_seconds=None)

    best_policy = None
    best_remaining = -2

    for ref_name in referrers:
        parent_obj_key = obj_prefix + ref_name
        parent_meta = ObjectMeta.load(r, parent_obj_key)
        if parent_meta is None:
            continue
        parent_policy = policy_from_meta(parent_meta)
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


def propagate_lifecycle(r, domain: str, namespace: str, parent_name: str, visited: set[str]) -> None:
    if parent_name in visited:
        return
    visited.add(parent_name)

    parent_obj_key = make_object_key(domain, namespace, parent_name)
    parent_meta = ObjectMeta.load(r, parent_obj_key)
    if parent_meta is None:
        return
    if not parent_meta.is_collection():
        return

    child_names = collect_ref_names_from_raw(r, parent_meta.value, parent_meta.type)
    for child_name in child_names:
        if not child_name.startswith("~"):
            continue
        child_obj_key = make_object_key(domain, namespace, child_name)
        child_meta = ObjectMeta.load(r, child_obj_key)
        if child_meta is None:
            continue
        if not child_meta.is_inherited():
            continue

        child_raw_key = child_meta.value if child_meta.is_collection() else ""
        child_ref_key = make_ref_key(domain, namespace, child_name)

        eff = effective_policy(child_meta, r, domain, namespace, child_name)
        apply_lifecycle(r, child_obj_key, child_raw_key, child_ref_key, child_meta.is_collection(), eff)
        propagate_lifecycle(r, domain, namespace, child_name, visited)

