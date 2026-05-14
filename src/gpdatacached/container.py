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

from .codec import GPDCScalarCodec, ListCodec, DictCodec
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

    def _load_ref_object(self, ref_str: str):
        obj_key = ref_str[len("ref:") :]
        parts = obj_key.split(":obj:")
        if len(parts) != 2:
            raise ValueError(f"Invalid ref string: {ref_str!r}")
        anon_name = parts[1]
        from .container import TYPE_CLASS_MAP

        r = self._redis()
        meta = r.hgetall(obj_key)
        if not meta:
            raise KeyError(f"Referenced object not found: {obj_key!r}")
        type_str = meta.get("type", "")
        cls = TYPE_CLASS_MAP.get(type_str)
        if cls is None:
            raise ValueError(f"Unknown GPDC type: {type_str!r}")
        obj = cls(anon_name, self._namespace)
        if isinstance(obj, GPDCContainerObject):
            stored_raw_key = meta.get("value", "")
            if stored_raw_key:
                obj.raw_key = stored_raw_key
        return obj

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
        result = []
        for item in items:
            if item.startswith("ref:"):
                result.append(self._load_ref_object(item))
            else:
                result.append(GPDCScalarCodec.decode_value(item))
        return result

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
        if encoded.startswith("ref:"):
            return self._load_ref_object(encoded)
        return GPDCScalarCodec.decode_value(encoded)

    def __setitem__(self, index: int, value: object) -> None:
        r = self._redis()
        length = r.llen(self.raw_key)
        if index < 0:
            index = length + index
        if index < 0 or index >= length:
            raise IndexError("list assignment index out of range")
        if isinstance(value, GPDCContainerObject):
            ref_val = self._namespace._ref_existing_container(value, self._name)
            r.lset(self.raw_key, index, ref_val)
        elif isinstance(value, (list, dict)):
            cache_mode = self.cache_mode
            ttl = self.ttl
            _, ref_val = self._namespace._create_anonymous_object(value, self._name, cache_mode, ttl)
            r.lset(self.raw_key, index, ref_val)
        else:
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
        if isinstance(value, GPDCContainerObject):
            items.insert(index, self._namespace._ref_existing_container(value, self._name))
        elif isinstance(value, (list, dict)):
            cache_mode = self.cache_mode
            ttl = self.ttl
            _, ref_val = self._namespace._create_anonymous_object(value, self._name, cache_mode, ttl)
            items.insert(index, ref_val)
        else:
            items.insert(index, GPDCScalarCodec.encode_value(value))
        r.delete(self.raw_key)
        if items:
            r.rpush(self.raw_key, *items)
        self._touch()

    def append(self, value: object) -> None:
        if isinstance(value, GPDCContainerObject):
            self._redis().rpush(self.raw_key, self._namespace._ref_existing_container(value, self._name))
        elif isinstance(value, (list, dict)):
            cache_mode = self.cache_mode
            ttl = self.ttl
            _, ref_val = self._namespace._create_anonymous_object(value, self._name, cache_mode, ttl)
            self._redis().rpush(self.raw_key, ref_val)
        else:
            encoded = GPDCScalarCodec.encode_value(value)
            self._redis().rpush(self.raw_key, encoded)
        self._touch()

    def extend(self, values) -> None:
        encoded = []
        for v in values:
            if isinstance(v, GPDCContainerObject):
                encoded.append(self._namespace._ref_existing_container(v, self._name))
            elif isinstance(v, (list, dict)):
                cache_mode = self.cache_mode
                ttl = self.ttl
                _, ref_val = self._namespace._create_anonymous_object(v, self._name, cache_mode, ttl)
                encoded.append(ref_val)
            else:
                encoded.append(GPDCScalarCodec.encode_value(v))
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

    def deepcopy(self):
        result = []
        for item in self._redis().lrange(self.raw_key, 0, -1):
            if item.startswith("ref:"):
                ref_obj = self._load_ref_object(item)
                if isinstance(ref_obj, GPDCContainerObject):
                    result.append(ref_obj.deepcopy())
                else:
                    result.append(ref_obj.value)
            else:
                result.append(GPDCScalarCodec.decode_value(item))
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
            if encoded_val.startswith("ref:"):
                result[key] = self._load_ref_object(encoded_val)
            else:
                result[key] = GPDCScalarCodec.decode_value(encoded_val)
        return result

    @value.setter
    def value(self, new_value: dict) -> None:
        r = self._redis()
        DictCodec.delete_raw(r, self.raw_key)
        DictCodec.create_raw(r, self.raw_key, new_value)
        self._touch()

    def __len__(self) -> int:
        return self._redis().hlen(self.raw_key)

    def __getitem__(self, key: object) -> object:
        encoded_key = GPDCScalarCodec.encode_dict_key(key)
        encoded_val = self._redis().hget(self.raw_key, encoded_key)
        if encoded_val is None:
            raise KeyError(key)
        if encoded_val.startswith("ref:"):
            return self._load_ref_object(encoded_val)
        return GPDCScalarCodec.decode_value(encoded_val)

    def __setitem__(self, key: object, value: object) -> None:
        encoded_key = GPDCScalarCodec.encode_dict_key(key)
        if isinstance(value, GPDCContainerObject):
            ref_val = self._namespace._ref_existing_container(value, self._name)
            self._redis().hset(self.raw_key, encoded_key, ref_val)
        elif isinstance(value, (list, dict)):
            cache_mode = self.cache_mode
            ttl = self.ttl
            _, ref_val = self._namespace._create_anonymous_object(value, self._name, cache_mode, ttl)
            self._redis().hset(self.raw_key, encoded_key, ref_val)
        else:
            encoded_val = GPDCScalarCodec.encode_value(value)
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
            if encoded_val.startswith("ref:"):
                result.append(self._load_ref_object(encoded_val))
            else:
                result.append(GPDCScalarCodec.decode_value(encoded_val))
        return result

    def items(self):
        result = []
        for encoded_key, encoded_val in self._redis().hgetall(self.raw_key).items():
            key = GPDCScalarCodec.decode_dict_key(encoded_key)
            if encoded_val.startswith("ref:"):
                result.append((key, self._load_ref_object(encoded_val)))
            else:
                result.append((key, GPDCScalarCodec.decode_value(encoded_val)))
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
            if encoded_val.startswith("ref:"):
                ref_obj = self._load_ref_object(encoded_val)
                if isinstance(ref_obj, GPDCContainerObject):
                    result[key] = ref_obj.deepcopy()
                else:
                    result[key] = ref_obj.value
            else:
                result[key] = GPDCScalarCodec.decode_value(encoded_val)
        return result


# ── Container type registry ───────────────────────────────────────────────────

CONTAINER_TYPE_CLASS_MAP: dict[str, type[GPDCContainerObject]] = {
    "collection:list": ListObject,
    "collection:dict": DictObject,
}

TYPE_CLASS_MAP: dict[str, type[GPDCDataObject]] = {
    **SCALAR_TYPE_CLASS_MAP,
    **CONTAINER_TYPE_CLASS_MAP,
}
