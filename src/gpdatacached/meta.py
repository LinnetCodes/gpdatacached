from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, model_validator


class ObjectMeta(BaseModel):
    type: str
    codec: str = "native"
    value: str = ""
    cache_mode: Literal["permanent", "ttl", "sliding", "inherited"] = "permanent"
    ttl: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    item_count: int | None = None

    @model_validator(mode="after")
    def _validate_ttl(self) -> ObjectMeta:
        if self.ttl < 0:
            raise ValueError("ttl must be >= 0")
        return self

    @classmethod
    def from_redis(cls, data: dict) -> ObjectMeta:
        cache_mode = data.get("cache_mode") or "permanent"
        ttl_str = data.get("ttl", "")
        ttl = int(ttl_str) if ttl_str else 0
        created_at_str = data.get("created_at")
        updated_at_str = data.get("updated_at")
        item_count_str = data.get("item_count")
        return cls(
            type=data.get("type", ""),
            codec=data.get("codec", "native"),
            value=data.get("value", ""),
            cache_mode=cache_mode,
            ttl=ttl,
            created_at=datetime.fromisoformat(created_at_str) if created_at_str else None,
            updated_at=datetime.fromisoformat(updated_at_str) if updated_at_str else None,
            item_count=int(item_count_str) if item_count_str is not None else None,
        )

    def to_redis_mapping(self) -> dict[str, str]:
        mapping: dict[str, str] = {
            "type": self.type,
            "codec": self.codec,
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
        return mapping

    def is_collection(self) -> bool:
        return self.type.startswith("collection:")

    def is_inherited(self) -> bool:
        return self.cache_mode == "inherited"

    def declares_expiry(self) -> bool:
        return self.cache_mode in ("ttl", "sliding") and self.ttl > 0
