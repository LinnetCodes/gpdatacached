# src/gpdatacached/container.py
"""GPDC container object hierarchy and type registry."""

from __future__ import annotations

import collections.abc
import datetime
import uuid
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .namespace import GPDCNamespace

from .codec import GPDCScalarCodec, ListCodec
from .keys import make_raw_key, make_ref_key
from .objects import GPDCDataObject
from .scalar import SCALAR_TYPE_CLASS_MAP


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

    @property
    def value(self) -> list:
        return ListCodec.read_raw(self._redis(), self.raw_key)

    @value.setter
    def value(self, new_value: list) -> None:
        r = self._redis()
        ListCodec.delete_raw(r, self.raw_key)
        ListCodec.create_raw(r, self.raw_key, new_value)
        self._touch()

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
