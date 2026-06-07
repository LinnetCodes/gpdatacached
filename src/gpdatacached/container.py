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

from .codec import DictCodec, ListCodec, SetCodec
from .keys import make_object_key, make_raw_key, make_ref_key
from .objects import GPDCDataObject
from .meta import ObjectMeta
from .ttl import policy_from_meta
from .types import encode_element, decode_element, encode_key, decode_key


class GPDCContainerObject(GPDCDataObject, ABC):
    """Abstract base class for all container GPDC objects."""

    def __init__(self, name: str, namespace: "GPDCNamespace", *, canonical_name: str | None = None) -> None:
        super().__init__(name, namespace, canonical_name=canonical_name)
        domain_name = namespace.domain.name
        ns_name = namespace.name
        self.raw_key: str = make_raw_key(domain_name, ns_name, name)
        self.ref_key: str = make_ref_key(domain_name, ns_name, name)

    def _touch(self) -> None:
        from .ttl import policy_from_meta, policy_for_sliding_touch
        from .meta import ObjectMeta
        from .keys import make_object_key
        from .exceptions import ObjectGoneError

        key = self._canonical_redis_key()
        now = datetime.datetime.now(datetime.UTC)
        r = self._redis()
        item_count = self._current_item_count()
        meta = ObjectMeta.load(r, key)
        if meta is None:
            raise ObjectGoneError(self.canonical_uri)
        meta.touch(now, item_count=item_count).save(r, key)

        if meta.owner == self.canonical_name:
            current = policy_from_meta(meta)
            new_policy = policy_for_sliding_touch(current)
            if new_policy != current:
                self.set_lifecycle(new_policy)
        else:
            owner_name = meta.owner
            while owner_name:
                owner_obj_key = make_object_key(self.domain.name, self.namespace.name, owner_name)
                owner_meta = ObjectMeta.load(r, owner_obj_key)
                if owner_meta is None:
                    break
                if owner_meta.owner == owner_name:
                    current = policy_from_meta(owner_meta)
                    new_policy = policy_for_sliding_touch(current)
                    if new_policy != current:
                        owner_obj = self.namespace.get_object(owner_name)
                        owner_obj.set_lifecycle(new_policy)
                    break
                owner_name = owner_meta.owner

    @abstractmethod
    def _current_item_count(self) -> int:
        """Return the current element count from Redis."""

    def _unbind_old_ref(self, old_encoded: str) -> None:
        if not isinstance(old_encoded, str) or not old_encoded.startswith("ref:"):
            return
        if self._owner_still_refs_encoded(old_encoded):
            return
        obj_key = old_encoded[len("ref:") :]
        parts = obj_key.split(":obj:")
        if len(parts) != 2:
            return
        target_name = parts[1]
        target_ref_key = make_ref_key(self._namespace.domain.name, self._namespace.name, target_name)
        r = self._redis()
        r.srem(target_ref_key, self._binding_name())
        if target_name.startswith("~") and not r.smembers(target_ref_key):
            target_obj_key = make_object_key(self._namespace.domain.name, self._namespace.name, target_name)
            target_meta = ObjectMeta.load(r, target_obj_key)
            if target_meta is None or target_meta.owner == self._binding_name():
                self._namespace._cascade_delete_anonymous(target_name)

    def _owner_still_refs_encoded(self, encoded: str) -> bool:
        if not self.raw_key:
            return False
        r = self._redis()
        redis_type = r.type(self.raw_key)
        if redis_type == "list":
            return encoded in r.lrange(self.raw_key, 0, -1)
        if redis_type == "hash":
            return encoded in r.hvals(self.raw_key)
        # "set" is intentionally not supported here: encode_key rejects container
        # types, so a set can never contain ref-encoded strings. See design doc.
        return False

    def _cleanup_slot_overwrite(self, old_encoded: str | None) -> None:
        if old_encoded is None:
            return
        if not self._is_container_ref(old_encoded):
            self._unbind_old_ref(old_encoded)
            return
        obj_key = old_encoded[len("ref:") :]
        parts = obj_key.split(":obj:")
        if len(parts) != 2:
            return
        target_name = parts[1]
        target_obj_key = make_object_key(self._namespace.domain.name, self._namespace.name, target_name)
        r = self._redis()
        old_meta = ObjectMeta.load(r, target_obj_key)
        if old_meta is not None and old_meta.owner != self._binding_name():
            self._unbind_old_ref(old_encoded)
            return
        self._unbind_old_ref(old_encoded)

    def _reject_value_replacement(self) -> None:
        # Extracted as a method to centralize the error message, called from 3 sites.
        raise TypeError("Container value replacement is not supported; use explicit mutating methods instead")

    def _is_container_ref(self, encoded: str | None) -> bool:
        return isinstance(encoded, str) and encoded.startswith("ref:")

    def _materialize_decoded_value(self, encoded: str) -> object:
        value = decode_element(encoded, self._namespace)
        if self._is_container_ref(encoded) and hasattr(value, "deepcopy"):
            return value.deepcopy()
        return value

    def _binding_name(self) -> str:
        if self._is_reference:
            return self._canonical_name
        meta = ObjectMeta.load(self._redis(), self._redis_key())
        if meta is None:
            return self._name
        raw_key = meta.value or self.raw_key
        prefix = f"gpdc:{self._namespace.domain.name}:{self._namespace.name}:raw:"
        if raw_key.startswith(prefix):
            self.raw_key = raw_key
            return raw_key[len(prefix) :]
        return self._name

    def _reference_obj_key(self) -> str:
        return make_object_key(self._namespace.domain.name, self._namespace.name, self._binding_name())

    def _reference_ref_value(self) -> str:
        return f"ref:{self._reference_obj_key()}"

    def referrers(self) -> set[str]:
        """Return set of object names referencing this object's raw data."""
        owner_name = self._binding_name()
        ref_key = make_ref_key(self._namespace.domain.name, self._namespace.name, owner_name)
        return self._redis().smembers(ref_key)

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
        return ListCodec.read_raw(self._namespace, self.raw_key)

    @value.setter
    def value(self, new_value: list) -> None:
        self._reject_value_replacement()

    def __len__(self) -> int:
        return self._redis().llen(self.raw_key)

    def _current_item_count(self) -> int:
        return self._redis().llen(self.raw_key)

    def _normalize_index(self, index: int, message: str = "list index out of range") -> int:
        if isinstance(index, slice):
            raise TypeError("ListObject does not support slice indexing; use list(obj)[start:stop] instead")
        length = self._redis().llen(self.raw_key)
        if index < 0:
            index = length + index
        if index < 0 or index >= length:
            raise IndexError(message)
        return index

    def __getitem__(self, index: int) -> object:
        r = self._redis()
        index = self._normalize_index(index)
        encoded = r.lindex(self.raw_key, index)
        if encoded is None:
            raise IndexError("list index out of range")
        return decode_element(encoded, self._namespace)

    def __setitem__(self, index: int, value: object) -> None:
        r = self._redis()
        index = self._normalize_index(index, "list assignment index out of range")
        old_encoded = r.lindex(self.raw_key, index)
        meta = ObjectMeta.load(r, self._canonical_redis_key())
        policy = policy_from_meta(meta) if meta else None
        encoded = encode_element(value, self._namespace, self._binding_name(), policy)
        r.lset(self.raw_key, index, encoded)
        self._cleanup_slot_overwrite(old_encoded)
        self._touch()

    def _delete_index(self, index: int) -> str:
        r = self._redis()
        index = self._normalize_index(index, "list assignment index out of range")
        old_encoded = r.lindex(self.raw_key, index)
        if old_encoded is None:
            raise IndexError("list assignment index out of range")
        sentinel = f"__del_sentinel_{uuid.uuid4().hex}__"
        r.lset(self.raw_key, index, sentinel)
        r.lrem(self.raw_key, 1, sentinel)
        self._unbind_old_ref(old_encoded)
        self._touch()
        return old_encoded

    def __delitem__(self, index: int) -> None:
        self._delete_index(index)

    def insert(self, index: int, value: object) -> None:
        r = self._redis()
        items = r.lrange(self.raw_key, 0, -1)
        meta = ObjectMeta.load(r, self._canonical_redis_key())
        policy = policy_from_meta(meta) if meta else None
        encoded = encode_element(value, self._namespace, self._binding_name(), policy)
        items.insert(index, encoded)
        pipe = r.pipeline()
        pipe.delete(self.raw_key)
        if items:
            pipe.rpush(self.raw_key, *items)
        pipe.execute()
        self._touch()

    def append(self, value: object) -> None:
        meta = self.meta
        policy = policy_from_meta(meta) if meta else None
        encoded = encode_element(value, self._namespace, self._binding_name(), policy)
        self._redis().rpush(self.raw_key, encoded)
        self._touch()

    def extend(self, values) -> None:
        binding_owner = self._binding_name()
        meta = self.meta
        policy = policy_from_meta(meta) if meta else None
        encoded = [encode_element(v, self._namespace, binding_owner, policy) for v in values]
        if encoded:
            self._redis().rpush(self.raw_key, *encoded)
        self._touch()

    def clear(self) -> None:
        items = self._redis().lrange(self.raw_key, 0, -1)
        self._redis().delete(self.raw_key)
        for item in items:
            self._unbind_old_ref(item)
        self._touch()

    def pop(self, index: int = -1) -> object:
        if index == -1:
            r = self._redis()
            encoded = r.lindex(self.raw_key, -1)
            if encoded is None:
                raise IndexError("pop from empty list")
            value = self._materialize_decoded_value(encoded)
            self._delete_index(-1)
            return value
        index = self._normalize_index(index, "pop index out of range")
        encoded = self._redis().lindex(self.raw_key, index)
        if encoded is None:
            raise IndexError("pop index out of range")
        value = self._materialize_decoded_value(encoded)
        self._delete_index(index)
        return value

    def remove(self, value: object) -> None:
        target_ref = None
        if isinstance(value, GPDCContainerObject):
            if value._namespace is not self._namespace:
                raise ValueError("Cross-namespace container reference sharing is not supported")
            target_ref = value._reference_ref_value()

        for index, encoded in enumerate(self._redis().lrange(self.raw_key, 0, -1)):
            if target_ref is not None and encoded == target_ref:
                self._delete_index(index)
                return
            if target_ref is None and decode_element(encoded, self._namespace) == value:
                self._delete_index(index)
                return

        raise ValueError(f"{value!r} is not in list")

    def reverse(self) -> None:
        r = self._redis()
        items = r.lrange(self.raw_key, 0, -1)
        items.reverse()
        pipe = r.pipeline()
        pipe.delete(self.raw_key)
        if items:
            pipe.rpush(self.raw_key, *items)
        pipe.execute()
        self._touch()

    def sort(self, *, key=None, reverse=False) -> None:
        r = self._redis()
        raw_items = r.lrange(self.raw_key, 0, -1)
        decoded = [decode_element(enc, self._namespace) for enc in raw_items]
        decoded.sort(key=key, reverse=reverse)
        binding_owner = self._binding_name()
        meta = ObjectMeta.load(r, self._canonical_redis_key())
        policy = policy_from_meta(meta) if meta else None
        encoded = [encode_element(v, self._namespace, binding_owner, policy) for v in decoded]
        pipe = r.pipeline()
        pipe.delete(self.raw_key)
        if encoded:
            pipe.rpush(self.raw_key, *encoded)
        pipe.execute()
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
        return [v.deepcopy() if hasattr(v, "deepcopy") else v for v in self.value]

    def __add__(self, other):
        """Not supported. Use ``list(obj) + other`` to concatenate."""
        raise NotImplementedError("ListObject does not support +; use list(obj) + other instead.")

    def __radd__(self, other):
        """Not supported. Use ``other + list(obj)`` to concatenate."""
        raise NotImplementedError("ListObject does not support +; use other + list(obj) instead.")

    def __mul__(self, other):
        """Not supported. Use ``list(obj) * n`` to repeat."""
        raise NotImplementedError("ListObject does not support *; use list(obj) * n instead.")

    def __rmul__(self, other):
        """Not supported. Use ``n * list(obj)`` to repeat."""
        raise NotImplementedError("ListObject does not support *; use n * list(obj) instead.")


# ── DictObject ─────────────────────────────────────────────────────────────────


class DictObject(GPDCContainerObject, collections.abc.MutableMapping):
    """Redis-backed dict object implementing MutableMapping."""

    TYPE = "collection:dict"

    @property
    def type(self) -> str:
        return self.TYPE

    @property
    def value(self) -> dict:
        return DictCodec.read_raw(self._namespace, self.raw_key)

    @value.setter
    def value(self, new_value: dict) -> None:
        self._reject_value_replacement()

    def __len__(self) -> int:
        return self._redis().hlen(self.raw_key)

    def _current_item_count(self) -> int:
        return self._redis().hlen(self.raw_key)

    def __getitem__(self, key: object) -> object:
        encoded_key = encode_key(key)
        encoded_val = self._redis().hget(self.raw_key, encoded_key)
        if encoded_val is None:
            raise KeyError(key)
        return decode_element(encoded_val, self._namespace)

    def __setitem__(self, key: object, value: object) -> None:
        encoded_key = encode_key(key)
        r = self._redis()
        old_encoded = r.hget(self.raw_key, encoded_key)
        meta = ObjectMeta.load(r, self._canonical_redis_key())
        policy = policy_from_meta(meta) if meta else None
        encoded_val = encode_element(value, self._namespace, self._binding_name(), policy)
        r.hset(self.raw_key, encoded_key, encoded_val)
        self._cleanup_slot_overwrite(old_encoded)
        self._touch()

    def __delitem__(self, key: object) -> None:
        encoded_key = encode_key(key)
        r = self._redis()
        old_encoded = r.hget(self.raw_key, encoded_key)
        if old_encoded is None:
            raise KeyError(key)
        r.hdel(self.raw_key, encoded_key)
        self._unbind_old_ref(old_encoded)
        self._touch()

    def __contains__(self, key: object) -> bool:
        encoded_key = encode_key(key)
        return self._redis().hexists(self.raw_key, encoded_key)

    def __iter__(self):
        for encoded_key in self._redis().hkeys(self.raw_key):
            yield decode_key(encoded_key)

    def keys(self) -> collections.abc.KeysView:
        """Return a frozen *KeysView* snapshot.

        Only keys are fetched from Redis (``HKEYS``); values are not decoded.
        The view is backed by a temporary ``dict`` so mutations to the
        ``DictObject`` after this call are **not** reflected.
        """
        decoded = [decode_key(k) for k in self._redis().hkeys(self.raw_key)]
        return dict.fromkeys(decoded).keys()

    def values(self) -> collections.abc.ValuesView:
        """Return a frozen *ValuesView* snapshot.

        All key-value pairs are fetched from Redis (``HGETALL``) and decoded.
        The view is backed by a temporary ``dict`` so mutations to the
        ``DictObject`` after this call are **not** reflected.
        """
        return self.value.values()

    def items(self) -> collections.abc.ItemsView:
        """Return a frozen *ItemsView* snapshot.

        All key-value pairs are fetched from Redis (``HGETALL``) and decoded.
        The view is backed by a temporary ``dict`` so mutations to the
        ``DictObject`` after this call are **not** reflected.
        """
        return self.value.items()

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    _SENTINEL = object()

    def pop(self, key, default=_SENTINEL):
        encoded_key = encode_key(key)
        r = self._redis()
        encoded_val = r.hget(self.raw_key, encoded_key)
        if encoded_val is None:
            if default is DictObject._SENTINEL:
                raise KeyError(key)
            return default
        value = self._materialize_decoded_value(encoded_val)
        del self[key]
        return value

    def setdefault(self, key, default=None):
        if key in self:
            return self[key]
        self[key] = default
        return self[key]

    def update(self, other=None, **kwargs):
        pairs = []
        if other is not None:
            if hasattr(other, "items"):
                pairs = list(other.items())
            else:
                pairs = list(other)
        pairs.extend(kwargs.items())
        if not pairs:
            return
        r = self._redis()
        binding_owner = self._binding_name()
        meta = ObjectMeta.load(r, self._canonical_redis_key())
        policy = policy_from_meta(meta) if meta else None
        dirty = False
        try:
            for k, v in pairs:
                encoded_key = encode_key(k)
                old_encoded = r.hget(self.raw_key, encoded_key)
                encoded_val = encode_element(v, self._namespace, binding_owner, policy)
                r.hset(self.raw_key, encoded_key, encoded_val)
                self._cleanup_slot_overwrite(old_encoded)
                dirty = True
        finally:
            if dirty:
                self._touch()

    def clear(self):
        encoded_vals = self._redis().hvals(self.raw_key)
        self._redis().delete(self.raw_key)
        for encoded_val in encoded_vals:
            self._unbind_old_ref(encoded_val)
        self._touch()

    def deepcopy(self):
        return {k: v.deepcopy() if hasattr(v, "deepcopy") else v for k, v in self.value.items()}


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
        self._reject_value_replacement()

    def __len__(self) -> int:
        return self._redis().scard(self.raw_key)

    def _current_item_count(self) -> int:
        return self._redis().scard(self.raw_key)

    def __contains__(self, item: object) -> bool:
        encoded = encode_key(item)
        return self._redis().sismember(self.raw_key, encoded)

    def __iter__(self):
        members = self._redis().smembers(self.raw_key)
        for m in members:
            yield decode_key(m)

    def add(self, value: object) -> None:
        encoded = encode_key(value)
        self._redis().sadd(self.raw_key, encoded)
        self._touch()

    def discard(self, value: object) -> None:
        encoded = encode_key(value)
        removed = self._redis().srem(self.raw_key, encoded)
        if removed:
            self._touch()

    def remove(self, value: object) -> None:
        encoded = encode_key(value)
        removed = self._redis().srem(self.raw_key, encoded)
        if not removed:
            raise KeyError(value)
        self._touch()

    def pop(self):
        encoded = self._redis().spop(self.raw_key)
        if encoded is None:
            raise KeyError("pop from an empty set")
        self._touch()
        return decode_key(encoded)

    def clear(self) -> None:
        self._redis().delete(self.raw_key)
        self._touch()

    def _to_encoded_set(self, other) -> set[str]:
        if isinstance(other, SetObject):
            return self._redis().smembers(other.raw_key)
        return {encode_key(e) for e in other}

    def _binop(self, other, op):
        self_set = self.value
        other_set = other.value if isinstance(other, SetObject) else set(other)
        return op(self_set, other_set)

    def __or__(self, other):
        """Return the union as a Python ``set`` (not a ``SetObject``).

        Returning a ``SetObject`` would require creating a new Redis key,
        which is an unexpected side-effect for a binary operator.
        Use ``obj |= other`` for in-place mutation backed by Redis.
        """
        return self._binop(other, set.__or__)

    def __and__(self, other):
        """Return the intersection as a Python ``set``.  See ``__or__``."""
        return self._binop(other, set.__and__)

    def __sub__(self, other):
        """Return the difference as a Python ``set``.  See ``__or__``."""
        return self._binop(other, set.__sub__)

    def __xor__(self, other):
        """Return the symmetric difference as a Python ``set``.  See ``__or__``."""
        return self._binop(other, set.__xor__)

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
