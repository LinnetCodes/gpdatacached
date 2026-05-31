from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ObjectMeta(BaseModel):
    type: str
    value: str = ""
    cache_mode: Literal["permanent", "ttl", "sliding", "inherited"] = "permanent"
    ttl: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    item_count: int | None = None
    ext: dict[str, str] = Field(default_factory=dict)

    def get_ext(self, key: str) -> str | None:
        return self.ext.get(key)

    def has_ext(self, key: str) -> bool:
        return key in self.ext

    def ext_keys(self) -> list[str]:
        return list(self.ext.keys())

    def set_ext(self, key: str, value: str) -> None:
        self.ext[key] = value

    def del_ext(self, key: str) -> None:
        self.ext.pop(key, None)

    def update_ext(self, data: dict[str, str]) -> None:
        self.ext.update(data)

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
