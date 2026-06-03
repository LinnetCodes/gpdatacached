from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


def _encode_extra_value(value: Any) -> str:
    from .types import GPDCTypeRegistry

    entry = GPDCTypeRegistry.lookup_by_python_type(value)
    if entry.is_container:
        raise TypeError(f"Container type {type(value).__name__!r} is not supported in extra metadata")
    return entry.codec_class.encode_value(value)


def _decode_extra_value(encoded: str) -> Any:
    if not encoded or ":" not in encoded:
        return encoded
    from .types import GPDCTypeRegistry

    colon_idx = encoded.index(":")
    type_name = encoded[:colon_idx]
    gpdc_type = f"scalar:{type_name}"
    try:
        entry = GPDCTypeRegistry.lookup_by_gpdc_type(gpdc_type)
    except ValueError:
        return encoded
    try:
        return entry.codec_class.decode_value(encoded)
    except ValueError:
        return encoded


class ObjectMeta(BaseModel):
    type: str
    value: str = ""
    cache_mode: Literal["permanent", "ttl", "sliding"] = "permanent"
    ttl: int = 0
    owner: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    item_count: int | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate(self) -> ObjectMeta:
        if self.ttl < 0:
            raise ValueError("ttl must be >= 0")
        for v in self.extra.values():
            _encode_extra_value(v)
        return self

    @classmethod
    def from_redis(cls, data: dict) -> ObjectMeta:
        builtin: dict[str, str] = {}
        extra_raw: dict[str, str] = {}
        for k, v in data.items():
            if k.startswith("$"):
                extra_raw[k[1:]] = v
            else:
                builtin[k] = v
        extra = {k: _decode_extra_value(v) for k, v in extra_raw.items()}
        cache_mode = builtin.get("cache_mode") or "permanent"
        ttl_str = builtin.get("ttl", "")
        ttl = int(ttl_str) if ttl_str else 0
        if cache_mode == "inherited":
            cache_mode = "permanent"
            ttl = 0
        created_at_str = builtin.get("created_at")
        updated_at_str = builtin.get("updated_at")
        item_count_str = builtin.get("item_count")
        return cls(
            type=builtin.get("type", ""),
            value=builtin.get("value", ""),
            cache_mode=cache_mode,
            ttl=ttl,
            owner=builtin.get("owner", ""),
            created_at=datetime.fromisoformat(created_at_str) if created_at_str else None,
            updated_at=datetime.fromisoformat(updated_at_str) if updated_at_str else None,
            item_count=int(item_count_str) if item_count_str is not None else None,
            extra=extra,
        )

    def to_redis_mapping(self) -> dict[str, str]:
        mapping: dict[str, str] = {
            "type": self.type,
            "value": self.value,
            "cache_mode": self.cache_mode,
            "ttl": str(self.ttl) if self.ttl else "",
        }
        if self.owner:
            mapping["owner"] = self.owner
        if self.created_at is not None:
            mapping["created_at"] = self.created_at.isoformat()
        if self.updated_at is not None:
            mapping["updated_at"] = self.updated_at.isoformat()
        if self.item_count is not None:
            mapping["item_count"] = str(self.item_count)
        for k, v in self.extra.items():
            mapping[f"${k}"] = _encode_extra_value(v)
        return mapping

    def is_collection(self) -> bool:
        return self.type.startswith("collection:")

    def declares_expiry(self) -> bool:
        return self.cache_mode in ("ttl", "sliding") and self.ttl > 0

    @classmethod
    def load(cls, r, obj_key: str) -> ObjectMeta | None:
        data = r.hgetall(obj_key)
        return cls.from_redis(data) if data else None

    def save(self, r, obj_key: str) -> None:
        r.hset(obj_key, mapping=self.to_redis_mapping())

    def with_updates(self, **kwargs) -> ObjectMeta:
        return self.model_validate(self.model_copy(update=kwargs))

    def touch(self, now: datetime, *, item_count: int | None = None) -> ObjectMeta:
        updates: dict = {"updated_at": now}
        if item_count is not None:
            updates["item_count"] = item_count
        return self.model_copy(update=updates)
