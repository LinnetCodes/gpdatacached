# src/gpdatacached/objects.py
"""GPDC data object hierarchy and scalar type registry."""
from __future__ import annotations

import datetime
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .domain import GPDCDomain
    from .namespace import GPDCNamespace

from .codec import GPDCScalarCodec
from .keys import make_object_key, make_uri


class GPDCDataObject(ABC):
    """Abstract base class for all GPDC data objects."""

    def __init__(self, name: str, namespace: "GPDCNamespace") -> None:
        self._name = name
        self._namespace = namespace

    # ── Identity ────────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return self._name

    @property
    def namespace(self) -> "GPDCNamespace":
        return self._namespace

    @property
    def domain(self) -> "GPDCDomain":
        return self._namespace.domain

    @property
    def uri(self) -> str:
        return make_uri(self.domain.name, self._namespace.name, self._name)

    @property
    @abstractmethod
    def type(self) -> str:
        """GPDC type string, e.g., 'scalar:int'."""

    @property
    @abstractmethod
    def value(self) -> object:
        """The Python value of this data object."""

    # ── Internal Redis helpers ───────────────────────────────────────────────

    def _redis_key(self) -> str:
        return make_object_key(self.domain.name, self._namespace.name, self._name)

    def _redis(self):
        return self.domain.redis

    def _hget(self, field: str) -> str:
        return self._redis().hget(self._redis_key(), field) or ""

    # ── Metadata properties ──────────────────────────────────────────────────

    @property
    def cache_mode(self) -> str:
        return self._hget("cache_mode")

    @cache_mode.setter
    def cache_mode(self, mode: str) -> None:
        if mode not in ("permanent", "ttl", "sliding"):
            raise ValueError(f"Invalid cache_mode: {mode!r}")
        key = self._redis_key()
        r = self._redis()
        r.hset(key, "cache_mode", mode)
        if mode == "permanent":
            r.persist(key)
        elif mode in ("ttl", "sliding"):
            ttl = self.ttl
            if ttl > 0:
                r.expire(key, ttl)

    @property
    def ttl(self) -> int:
        raw = self._hget("ttl")
        return int(raw) if raw else 0

    @ttl.setter
    def ttl(self, seconds: int) -> None:
        key = self._redis_key()
        r = self._redis()
        r.hset(key, "ttl", str(seconds))
        if self.cache_mode in ("ttl", "sliding") and seconds > 0:
            r.expire(key, seconds)

    @property
    def created_at(self) -> datetime.datetime | None:
        raw = self._hget("created_at")
        return datetime.datetime.fromisoformat(raw) if raw else None

    @property
    def updated_at(self) -> datetime.datetime | None:
        raw = self._hget("updated_at")
        return datetime.datetime.fromisoformat(raw) if raw else None

    # ── TTL management ───────────────────────────────────────────────────────

    def expire_in(self, seconds: int) -> None:
        """Set TTL from now (in seconds)."""
        self._redis().expire(self._redis_key(), seconds)

    def expire_at(self, when: datetime.datetime) -> None:
        """Set absolute expiration time."""
        self._redis().expireat(self._redis_key(), when)

    def persist(self) -> None:
        """Remove TTL; make the key permanent."""
        self._redis().persist(self._redis_key())

    def remaining_ttl(self) -> int:
        """Return remaining TTL in seconds. -1 = permanent, -2 = key not found."""
        return self._redis().ttl(self._redis_key())


class GPDCScalarObject(GPDCDataObject):
    """Base class for all scalar GPDC objects."""

    # Override in each subclass
    TYPE: str = ""

    @property
    def type(self) -> str:
        return self.__class__.TYPE

    @property
    def value(self) -> object:
        """Read and decode the stored scalar value from Redis."""
        encoded = self._hget("value")
        if not encoded:
            return None
        return GPDCScalarCodec.decode_value(encoded)

    @value.setter
    def value(self, new_value: object) -> None:
        """Encode and persist a new scalar value; update updated_at and slide TTL."""
        encoded = GPDCScalarCodec.encode_value(new_value)
        key = self._redis_key()
        now = datetime.datetime.now(datetime.UTC).isoformat()
        r = self._redis()
        r.hset(key, mapping={"value": encoded, "updated_at": now})
        if self.cache_mode == "sliding":
            ttl = self.ttl
            if ttl > 0:
                r.expire(key, ttl)


# ── Scalar type subclasses ────────────────────────────────────────────────────

class IntObject(GPDCScalarObject):
    TYPE = "scalar:int"


class FloatObject(GPDCScalarObject):
    TYPE = "scalar:float"


class StrObject(GPDCScalarObject):
    TYPE = "scalar:str"


class BoolObject(GPDCScalarObject):
    TYPE = "scalar:bool"


class DateTimeObject(GPDCScalarObject):
    TYPE = "scalar:datetime"


class DateObject(GPDCScalarObject):
    TYPE = "scalar:date"


class TimeObject(GPDCScalarObject):
    TYPE = "scalar:time"


class NoneObject(GPDCScalarObject):
    TYPE = "scalar:none"


# ── Type registry ─────────────────────────────────────────────────────────────

SCALAR_TYPE_CLASS_MAP: dict[str, type[GPDCScalarObject]] = {
    "scalar:int": IntObject,
    "scalar:float": FloatObject,
    "scalar:str": StrObject,
    "scalar:bool": BoolObject,
    "scalar:datetime": DateTimeObject,
    "scalar:date": DateObject,
    "scalar:time": TimeObject,
    "scalar:none": NoneObject,
}
