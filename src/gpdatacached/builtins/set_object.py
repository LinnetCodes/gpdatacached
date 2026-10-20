from __future__ import annotations

import collections.abc
from typing import cast

from ..codec import GPDCContainerCodec
from ..container import GPDCContainerObject
from ..types import decode_key, encode_key


class SetCodec(GPDCContainerCodec):
    """Codec for Python set values stored as Redis Sets."""

    @classmethod
    def create_raw(cls, namespace, raw_key: str, value: object, owner_name: str, cache_mode: str, ttl: int) -> None:
        # Codec API contract is ``object`` (matches GPDCContainerCodec); this
        # codec's values are sets — the runtime caller guaranteed that via
        # the type registry before reaching here.
        members = cast("set[object]", value)
        if not members:
            return
        from ..types import encode_key

        encoded = [encode_key(elem) for elem in members]
        namespace._redis().sadd(raw_key, *encoded)

    @classmethod
    def read_raw(cls, namespace, raw_key: str) -> set:
        from ..types import decode_key

        members = namespace._redis().smembers(raw_key)
        if not members:
            return set()
        return {decode_key(m) for m in members}

    @classmethod
    def delete_raw(cls, namespace, raw_key: str) -> None:
        namespace._redis().delete(raw_key)


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
    def value(self, new_value: object) -> None:
        self._reject_value_replacement()

    def __len__(self) -> int:
        return self._redis().scard(self.raw_key)

    def __contains__(self, item: object) -> bool:
        encoded = encode_key(item)
        return self._redis().sismember(self.raw_key, encoded)

    def __iter__(self):
        members = self._redis().smembers(self.raw_key)
        for m in members:
            yield decode_key(m)

    def add(self, value: object) -> None:
        self._check_alive()
        encoded = encode_key(value)
        self._redis().sadd(self.raw_key, encoded)
        self._touch()

    def discard(self, value: object) -> None:
        self._check_alive()
        encoded = encode_key(value)
        removed = self._redis().srem(self.raw_key, encoded)
        if removed:
            self._touch()

    def remove(self, value: object) -> None:
        self._check_alive()
        encoded = encode_key(value)
        removed = self._redis().srem(self.raw_key, encoded)
        if not removed:
            raise KeyError(value)
        self._touch()

    def pop(self):
        self._check_alive()
        encoded = self._redis().spop(self.raw_key)
        if encoded is None:
            raise KeyError("pop from an empty set")
        self._touch()
        return decode_key(encoded)

    def clear(self) -> None:
        self._check_alive()
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
        self._check_alive()
        other_encoded = self._to_encoded_set(other)
        if other_encoded:
            self._redis().sadd(self.raw_key, *other_encoded)
        self._touch()
        return self

    def __iand__(self, other):
        self._check_alive()
        other_encoded = self._to_encoded_set(other)
        current = self._redis().smembers(self.raw_key)
        to_remove = current - other_encoded
        if to_remove:
            self._redis().srem(self.raw_key, *to_remove)
        self._touch()
        return self

    def __isub__(self, other):
        self._check_alive()
        other_encoded = self._to_encoded_set(other)
        if other_encoded:
            self._redis().srem(self.raw_key, *other_encoded)
        self._touch()
        return self

    def __ixor__(self, other):
        self._check_alive()
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
