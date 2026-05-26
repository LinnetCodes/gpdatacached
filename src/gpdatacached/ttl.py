# src/gpdatacached/ttl.py
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

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


def policy_from_meta(meta: dict) -> TTLPolicy:
    cache_mode = meta.get("cache_mode") or "permanent"
    ttl_str = meta.get("ttl", "")
    declared_ttl = int(ttl_str) if ttl_str else 0
    if cache_mode in ("ttl", "sliding") and declared_ttl > 0:
        return TTLPolicy(cache_mode=cache_mode, declared_ttl=declared_ttl, expire_seconds=declared_ttl)
    return TTLPolicy(cache_mode=cache_mode, declared_ttl=declared_ttl, expire_seconds=None)


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


def meta_ttl_value(policy: TTLPolicy) -> str:
    return str(policy.declared_ttl) if policy.declared_ttl else ""
