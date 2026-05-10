# src/gpdatacached/objects.py
"""GPDC data object hierarchy and scalar type registry."""

from __future__ import annotations

import collections.abc
import datetime
import uuid
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .domain import GPDCDomain
    from .namespace import GPDCNamespace

from .codec import GPDCScalarCodec, ListCodec
from .keys import make_object_key, make_raw_key, make_ref_key, make_uri


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


# ── Container object base ──────────────────────────────────────────────────────


class GPDCContainerObject(GPDCDataObject, ABC):
    """Abstract base class for all container GPDC objects."""

    def __init__(self, name: str, namespace: "GPDCNamespace") -> None:
        super().__init__(name, namespace)
        domain_name = namespace.domain.name
        ns_name = namespace.name
        self.raw_key: str = make_raw_key(domain_name, ns_name, name)
        self.ref_key: str = make_ref_key(domain_name, ns_name, name)

    def _touch(self) -> None:
        """Update updated_at and slide TTL if needed."""
        key = self._redis_key()
        now = datetime.datetime.now(datetime.UTC).isoformat()
        r = self._redis()
        r.hset(key, "updated_at", now)
        if self.cache_mode == "sliding":
            ttl = self.ttl
            if ttl > 0:
                r.expire(key, ttl)

    def referrers(self) -> set[str]:
        """Return set of object names referencing this object's raw data."""
        return self._redis().smembers(self.ref_key)

    def copy(self) -> object:
        """Shallow copy: returns Python value (raw data)."""
        return self.value

    def deepcopy(self) -> object:
        """Deep copy: same as copy() in this phase."""
        return self.value

    @property
    @abstractmethod
    def value(self) -> object: ...

    @value.setter
    @abstractmethod
    def value(self, new_value: object) -> None: ...


# ── ListObject ────────────────────────────────────────────────────────────────


class ListObject(GPDCContainerObject, collections.abc.MutableSequence):
    """Redis-backed list object implementing MutableSequence."""

    TYPE = "collection:list"

    @property
    def type(self) -> str:
        return self.TYPE

    # ── value property ────────────────────────────────────────────────────────

    @property
    def value(self) -> list:
        return ListCodec.read_raw(self._redis(), self.raw_key)

    @value.setter
    def value(self, new_value: list) -> None:
        r = self._redis()
        ListCodec.delete_raw(r, self.raw_key)
        ListCodec.create_raw(r, self.raw_key, new_value)
        self._touch()

    # ── MutableSequence abstract methods ──────────────────────────────────────

    def __len__(self) -> int:
        return self._redis().llen(self.raw_key)

    def __getitem__(self, index: int) -> object:
        r = self._redis()
        length = r.llen(self.raw_key)
        if index < 0:
            index = length + index
        if index < 0 or index >= length:
            raise IndexError("list index out of range")
        encoded = r.lindex(self.raw_key, index)
        if encoded is None:
            raise IndexError("list index out of range")
        return GPDCScalarCodec.decode_value(encoded)

    def __setitem__(self, index: int, value: object) -> None:
        r = self._redis()
        length = r.llen(self.raw_key)
        if index < 0:
            index = length + index
        if index < 0 or index >= length:
            raise IndexError("list assignment index out of range")
        encoded = GPDCScalarCodec.encode_value(value)
        r.lset(self.raw_key, index, encoded)
        self._touch()

    def __delitem__(self, index: int) -> None:
        r = self._redis()
        length = r.llen(self.raw_key)
        if index < 0:
            index = length + index
        if index < 0 or index >= length:
            raise IndexError("list assignment index out of range")
        sentinel = f"__del_sentinel_{uuid.uuid4().hex}__"
        r.lset(self.raw_key, index, sentinel)
        r.lrem(self.raw_key, 1, sentinel)
        self._touch()

    def insert(self, index: int, value: object) -> None:
        r = self._redis()
        items = r.lrange(self.raw_key, 0, -1)
        encoded = GPDCScalarCodec.encode_value(value)
        items.insert(index, encoded)
        r.delete(self.raw_key)
        if items:
            r.rpush(self.raw_key, *items)
        self._touch()

    # ── Efficient overrides ───────────────────────────────────────────────────

    def append(self, value: object) -> None:
        encoded = GPDCScalarCodec.encode_value(value)
        self._redis().rpush(self.raw_key, encoded)
        self._touch()

    def extend(self, values) -> None:
        encoded = [GPDCScalarCodec.encode_value(v) for v in values]
        if encoded:
            self._redis().rpush(self.raw_key, *encoded)
        self._touch()

    def clear(self) -> None:
        self._redis().delete(self.raw_key)
        self._touch()

    def pop(self, index: int = -1) -> object:
        r = self._redis()
        if index == -1:
            encoded = r.rpop(self.raw_key)
            if encoded is None:
                raise IndexError("pop from empty list")
            self._touch()
            return GPDCScalarCodec.decode_value(encoded)
        # custom pop at index
        val = self[index]
        del self[index]
        return val

    def remove(self, value: object) -> None:
        encoded = GPDCScalarCodec.encode_value(value)
        removed = self._redis().lrem(self.raw_key, 1, encoded)
        if removed == 0:
            raise ValueError(f"{value!r} is not in list")
        self._touch()

    def reverse(self) -> None:
        r = self._redis()
        items = r.lrange(self.raw_key, 0, -1)
        items.reverse()
        r.delete(self.raw_key)
        if items:
            r.rpush(self.raw_key, *items)
        self._touch()

    def sort(self, *, key=None, reverse=False) -> None:
        r = self._redis()
        raw_items = r.lrange(self.raw_key, 0, -1)
        decoded = [GPDCScalarCodec.decode_value(enc) for enc in raw_items]
        decoded.sort(key=key, reverse=reverse)
        encoded = [GPDCScalarCodec.encode_value(v) for v in decoded]
        r.delete(self.raw_key)
        if encoded:
            r.rpush(self.raw_key, *encoded)
        self._touch()

    def count(self, value: object) -> int:
        items = self.value
        return items.count(value)

    def index(self, value: object, start: int = 0, stop: int = None) -> int:  # type: ignore[override]
        items = self.value
        if stop is None:
            return items.index(value, start)
        return items.index(value, start, stop)


# ── Container type registry ───────────────────────────────────────────────────

CONTAINER_TYPE_CLASS_MAP: dict[str, type[GPDCContainerObject]] = {
    "collection:list": ListObject,
}

TYPE_CLASS_MAP: dict[str, type[GPDCDataObject]] = {
    **SCALAR_TYPE_CLASS_MAP,
    **CONTAINER_TYPE_CLASS_MAP,
}
