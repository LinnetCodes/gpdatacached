from __future__ import annotations

import collections.abc
import uuid

from ..codec import GPDCContainerCodec
from ..container import GPDCContainerObject
from ..meta import ObjectMeta
from ..ttl import policy_from_meta
from ..types import decode_element, encode_element


class ListCodec(GPDCContainerCodec):
    """Codec for Python list values stored as Redis lists."""

    @classmethod
    def create_raw(cls, namespace, raw_key: str, value: list, owner_name: str, cache_mode: str, ttl: int) -> None:
        from ..ttl import policy_from_defaults
        from ..types import encode_element

        if not value:
            return
        policy = policy_from_defaults(cache_mode, ttl)
        encoded = [encode_element(v, namespace, owner_name, policy) for v in value]
        namespace._redis().rpush(raw_key, *encoded)

    @classmethod
    def read_raw(cls, namespace, raw_key: str) -> list:
        from ..types import decode_element

        items = namespace._redis().lrange(raw_key, 0, -1)
        return [decode_element(item, namespace) for item in items]

    @classmethod
    def delete_raw(cls, namespace, raw_key: str) -> None:
        namespace._redis().delete(raw_key)


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
        encoded = encode_element(value, self._namespace, self._data_owner_name(), policy)
        r.lset(self.raw_key, index, encoded)
        self._release_element_ref(old_encoded)
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
        self._release_element_ref(old_encoded)
        self._touch()
        return old_encoded

    def __delitem__(self, index: int) -> None:
        self._delete_index(index)

    def insert(self, index: int, value: object) -> None:
        r = self._redis()
        items = r.lrange(self.raw_key, 0, -1)
        meta = ObjectMeta.load(r, self._canonical_redis_key())
        policy = policy_from_meta(meta) if meta else None
        encoded = encode_element(value, self._namespace, self._data_owner_name(), policy)
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
        encoded = encode_element(value, self._namespace, self._data_owner_name(), policy)
        self._redis().rpush(self.raw_key, encoded)
        self._touch()

    def extend(self, values) -> None:
        binding_owner = self._data_owner_name()
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
            self._release_element_ref(item)
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
        binding_owner = self._data_owner_name()
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
