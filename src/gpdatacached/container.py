# src/gpdatacached/container.py
"""GPDC container object hierarchy."""

from __future__ import annotations

import collections.abc
import datetime
import uuid
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .namespace import GPDCNamespace

from .codec import GPDCScalarCodec, ListCodec, DictCodec, SetCodec
from .keys import make_raw_key, make_ref_key
from ._base import GPDCDataObject
from .types import encode_element, decode_element


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
        """Shallow copy: return Python value with shared anon object refs."""
        return self.value

    def deepcopy(self) -> object:
        """Deep copy: recursively clone all anon objects, return Python value."""
        raise NotImplementedError("Subclass must implement deepcopy()")

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
        r = self._redis()
        items = r.lrange(self.raw_key, 0, -1)
        return [decode_element(item, self._namespace) for item in items]

    @value.setter
    def value(self, new_value: list) -> None:
        ListCodec.delete_raw(self._namespace, self.raw_key)
        ListCodec.create_raw(self._namespace, self.raw_key, new_value, self._name, self.cache_mode, self.ttl)
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
        return decode_element(encoded, self._namespace)

    def __setitem__(self, index: int, value: object) -> None:
        r = self._redis()
        length = r.llen(self.raw_key)
        if index < 0:
            index = length + index
        if index < 0 or index >= length:
            raise IndexError("list assignment index out of range")
        encoded = encode_element(value, self._namespace, self._name, self.cache_mode, self.ttl)
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
        encoded = encode_element(value, self._namespace, self._name, self.cache_mode, self.ttl)
        items.insert(index, encoded)
        r.delete(self.raw_key)
        if items:
            r.rpush(self.raw_key, *items)
        self._touch()

    def append(self, value: object) -> None:
        encoded = encode_element(value, self._namespace, self._name, self.cache_mode, self.ttl)
        self._redis().rpush(self.raw_key, encoded)
        self._touch()

    def extend(self, values) -> None:
        encoded = [encode_element(v, self._namespace, self._name, self.cache_mode, self.ttl) for v in values]
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
            return decode_element(encoded, self._namespace)
        val = self[index]
        del self[index]
        return val

    def remove(self, value: object) -> None:
        encoded = encode_element(value, self._namespace, self._name, self.cache_mode, self.ttl)
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
        decoded = [decode_element(enc, self._namespace) for enc in raw_items]
        decoded.sort(key=key, reverse=reverse)
        encoded = [encode_element(v, self._namespace, self._name, self.cache_mode, self.ttl) for v in decoded]
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

    def deepcopy(self):
        result = []
        for item in self._redis().lrange(self.raw_key, 0, -1):
            decoded = decode_element(item, self._namespace)
            if hasattr(decoded, "deepcopy"):
                result.append(decoded.deepcopy())
            else:
                result.append(decoded)
        return result


# ── DictObject ─────────────────────────────────────────────────────────────────


class DictObject(GPDCContainerObject, collections.abc.MutableMapping):
    """Redis-backed dict object implementing MutableMapping."""

    TYPE = "collection:dict"

    @property
    def type(self) -> str:
        return self.TYPE

    @property
    def value(self) -> dict:
        r = self._redis()
        data = r.hgetall(self.raw_key)
        if not data:
            return {}
        result = {}
        for encoded_key, encoded_val in data.items():
            key = GPDCScalarCodec.decode_dict_key(encoded_key)
            result[key] = decode_element(encoded_val, self._namespace)
        return result

    @value.setter
    def value(self, new_value: dict) -> None:
        DictCodec.delete_raw(self._namespace, self.raw_key)
        DictCodec.create_raw(self._namespace, self.raw_key, new_value, self._name, self.cache_mode, self.ttl)
        self._touch()

    def __len__(self) -> int:
        return self._redis().hlen(self.raw_key)

    def __getitem__(self, key: object) -> object:
        encoded_key = GPDCScalarCodec.encode_dict_key(key)
        encoded_val = self._redis().hget(self.raw_key, encoded_key)
        if encoded_val is None:
            raise KeyError(key)
        return decode_element(encoded_val, self._namespace)

    def __setitem__(self, key: object, value: object) -> None:
        encoded_key = GPDCScalarCodec.encode_dict_key(key)
        encoded_val = encode_element(value, self._namespace, self._name, self.cache_mode, self.ttl)
        self._redis().hset(self.raw_key, encoded_key, encoded_val)
        self._touch()

    def __delitem__(self, key: object) -> None:
        encoded_key = GPDCScalarCodec.encode_dict_key(key)
        existed = self._redis().hdel(self.raw_key, encoded_key)
        if not existed:
            raise KeyError(key)
        self._touch()

    def __contains__(self, key: object) -> bool:
        encoded_key = GPDCScalarCodec.encode_dict_key(key)
        return self._redis().hexists(self.raw_key, encoded_key)

    def __iter__(self):
        for encoded_key in self._redis().hkeys(self.raw_key):
            yield GPDCScalarCodec.decode_dict_key(encoded_key)

    def keys(self):
        return [GPDCScalarCodec.decode_dict_key(k) for k in self._redis().hkeys(self.raw_key)]

    def values(self):
        result = []
        for encoded_val in self._redis().hvals(self.raw_key):
            result.append(decode_element(encoded_val, self._namespace))
        return result

    def items(self):
        result = []
        for encoded_key, encoded_val in self._redis().hgetall(self.raw_key).items():
            key = GPDCScalarCodec.decode_dict_key(encoded_key)
            result.append((key, decode_element(encoded_val, self._namespace)))
        return result

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def pop(self, key, *args):
        try:
            val = self[key]
            del self[key]
            return val
        except KeyError:
            if args:
                return args[0]
            raise

    def setdefault(self, key, default=None):
        if key in self:
            return self[key]
        self[key] = default
        return default

    def update(self, other=None, **kwargs):
        if other:
            if hasattr(other, "items"):
                for k, v in other.items():
                    self[k] = v
            else:
                for k, v in other:
                    self[k] = v
        for k, v in kwargs.items():
            self[k] = v

    def clear(self):
        self._redis().delete(self.raw_key)
        self._touch()

    def deepcopy(self):
        result = {}
        for encoded_key, encoded_val in self._redis().hgetall(self.raw_key).items():
            key = GPDCScalarCodec.decode_dict_key(encoded_key)
            decoded = decode_element(encoded_val, self._namespace)
            if hasattr(decoded, "deepcopy"):
                result[key] = decoded.deepcopy()
            else:
                result[key] = decoded
        return result


# ── SetObject ──────────────────────────────────────────────────────────────────


class SetObject(GPDCContainerObject, collections.abc.MutableSet):
    """Redis-backed set object implementing MutableSet."""

    TYPE = "collection:set"

    @property
    def type(self) -> str:
        return self.TYPE

    @property
    def value(self) -> set:
        return SetCodec.read_raw(self._namespace, self.raw_key)

    @value.setter
    def value(self, new_value: set) -> None:
        SetCodec.delete_raw(self._namespace, self.raw_key)
        SetCodec.create_raw(self._namespace, self.raw_key, new_value, self._name, self.cache_mode, self.ttl)
        self._touch()

    def __len__(self) -> int:
        return self._redis().scard(self.raw_key)

    def __contains__(self, item: object) -> bool:
        encoded = SetCodec._encode_element(item)
        return self._redis().sismember(self.raw_key, encoded)

    def __iter__(self):
        members = self._redis().smembers(self.raw_key)
        for m in members:
            yield SetCodec._decode_element(m)

    def add(self, value: object) -> None:
        encoded = SetCodec._encode_element(value)
        self._redis().sadd(self.raw_key, encoded)
        self._touch()

    def discard(self, value: object) -> None:
        encoded = SetCodec._encode_element(value)
        self._redis().srem(self.raw_key, encoded)
        self._touch()

    def remove(self, value: object) -> None:
        encoded = SetCodec._encode_element(value)
        removed = self._redis().srem(self.raw_key, encoded)
        if not removed:
            raise KeyError(value)
        self._touch()

    def pop(self):
        encoded = self._redis().spop(self.raw_key)
        if encoded is None:
            raise KeyError("pop from an empty set")
        self._touch()
        return SetCodec._decode_element(encoded)

    def clear(self) -> None:
        self._redis().delete(self.raw_key)
        self._touch()

    def _to_encoded_set(self, other) -> set[str]:
        if isinstance(other, SetObject):
            return self._redis().smembers(other.raw_key)
        return {SetCodec._encode_element(e) for e in other}

    def __or__(self, other):
        self_set = self.value
        if isinstance(other, SetObject):
            other_set = other.value
        else:
            other_set = set(other)
        return self_set | other_set

    def __and__(self, other):
        self_set = self.value
        if isinstance(other, SetObject):
            other_set = other.value
        else:
            other_set = set(other)
        return self_set & other_set

    def __sub__(self, other):
        self_set = self.value
        if isinstance(other, SetObject):
            other_set = other.value
        else:
            other_set = set(other)
        return self_set - other_set

    def __xor__(self, other):
        self_set = self.value
        if isinstance(other, SetObject):
            other_set = other.value
        else:
            other_set = set(other)
        return self_set ^ other_set

    def __ior__(self, other):
        other_encoded = self._to_encoded_set(other)
        if other_encoded:
            self._redis().sadd(self.raw_key, *other_encoded)
        self._touch()
        return self

    def __iand__(self, other):
        other_encoded = self._to_encoded_set(other)
        current = self._redis().smembers(self.raw_key)
        to_remove = current - other_encoded
        if to_remove:
            self._redis().srem(self.raw_key, *to_remove)
        self._touch()
        return self

    def __isub__(self, other):
        other_encoded = self._to_encoded_set(other)
        if other_encoded:
            self._redis().srem(self.raw_key, *other_encoded)
        self._touch()
        return self

    def __ixor__(self, other):
        other_encoded = self._to_encoded_set(other)
        current = self._redis().smembers(self.raw_key)
        to_add = other_encoded - current
        to_remove = current & other_encoded
        if to_remove:
            self._redis().srem(self.raw_key, *to_remove)
        if to_add:
            self._redis().sadd(self.raw_key, *to_add)
        self._touch()
        return self

    def deepcopy(self):
        return self.value
