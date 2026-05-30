from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, model_validator


class ObjectMeta(BaseModel):
    type: str
    value: str = ""
    cache_mode: Literal["permanent", "ttl", "sliding", "inherited"] = "permanent"
    ttl: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    item_count: int | None = None
    ext: dict[str, str] = {}

    @model_validator(mode="after")
    def _validate_ttl(self) -> ObjectMeta:
        if self.ttl < 0:
            raise ValueError("ttl must be >= 0")
        return self

    @classmethod
    def from_redis(cls, data: dict) -> ObjectMeta:
        builtin: dict[str, str] = {}
        ext: dict[str, str] = {}
        for k, v in data.items():
            if k.startswith("$"):
                ext[k[1:]] = v
            else:
                builtin[k] = v
        cache_mode = builtin.get("cache_mode") or "permanent"
        ttl_str = builtin.get("ttl", "")
        ttl = int(ttl_str) if ttl_str else 0
        created_at_str = builtin.get("created_at")
        updated_at_str = builtin.get("updated_at")
        item_count_str = builtin.get("item_count")
        return cls(
            type=builtin.get("type", ""),
            value=builtin.get("value", ""),
            cache_mode=cache_mode,
            ttl=ttl,
            created_at=datetime.fromisoformat(created_at_str) if created_at_str else None,
            updated_at=datetime.fromisoformat(updated_at_str) if updated_at_str else None,
            item_count=int(item_count_str) if item_count_str is not None else None,
            ext=ext,
        )

    def to_redis_mapping(self) -> dict[str, str]:
        mapping: dict[str, str] = {
            "type": self.type,
            "value": self.value,
            "cache_mode": self.cache_mode,
            "ttl": str(self.ttl) if self.ttl else "",
        }
        if self.created_at is not None:
            mapping["created_at"] = self.created_at.isoformat()
        if self.updated_at is not None:
            mapping["updated_at"] = self.updated_at.isoformat()
        if self.item_count is not None:
            mapping["item_count"] = str(self.item_count)
        for k, v in self.ext.items():
            mapping[f"${k}"] = v
        return mapping

    def is_collection(self) -> bool:
        return self.type.startswith("collection:")

    def is_inherited(self) -> bool:
        return self.cache_mode == "inherited"

    def declares_expiry(self) -> bool:
        return self.cache_mode in ("ttl", "sliding") and self.ttl > 0

    @classmethod
    def load(cls, r, obj_key: str) -> ObjectMeta | None:
        data = r.hgetall(obj_key)
        return cls.from_redis(data) if data else None

    def save(self, r, obj_key: str) -> None:
        r.hset(obj_key, mapping=self.to_redis_mapping())

    def with_updates(self, **kwargs) -> ObjectMeta:
        return self.model_copy(update=kwargs)

    def touch(self, now: datetime, *, item_count: int | None = None) -> ObjectMeta:
        updates: dict = {"updated_at": now}
        if item_count is not None:
            updates["item_count"] = item_count
        return self.model_copy(update=updates)


def apply_lifecycle(r, obj_key: str, raw_key: str, ref_key: str, is_collection: bool, policy) -> None:
    from .ttl import apply_policy

    apply_policy(r, obj_key, policy)
    if is_collection:
        if raw_key:
            apply_policy(r, raw_key, policy)
        if ref_key:
            apply_policy(r, ref_key, policy)


def effective_policy(meta: ObjectMeta, r, domain: str, namespace: str, obj_name: str):
    from .keys import make_ref_key
    from .ttl import TTLPolicy, policy_from_meta

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
    from .keys import make_object_key, make_ref_key

    if parent_name in visited:
        return
    visited.add(parent_name)

    parent_obj_key = make_object_key(domain, namespace, parent_name)
    parent_meta = ObjectMeta.load(r, parent_obj_key)
    if parent_meta is None:
        return
    if not parent_meta.is_collection():
        return

    child_names = _collect_child_names(r, parent_meta.value, parent_meta.type)
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


def _collect_child_names(r, raw_key: str, raw_type: str) -> list[str]:
    if not raw_key:
        return []
    names: list[str] = []
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
