from __future__ import annotations

import collections.abc

from ..codec import GPDCContainerCodec
from ..container import GPDCContainerObject
from ..meta import ObjectMeta
from ..lifecycle import policy_from_meta
from ..types import decode_element, decode_key, encode_element, encode_key


class DictCodec(GPDCContainerCodec):
    """Codec for Python dict values stored as Redis Hashes."""

    @classmethod
    def create_raw(cls, namespace, raw_key: str, value: dict, owner_name: str, cache_mode: str, ttl: int) -> None:
        from ..lifecycle import policy_from_defaults
        from ..types import encode_element, encode_key

        if not value:
            return
        policy = policy_from_defaults(cache_mode, ttl)
        mapping = {}
        for k, v in value.items():
            encoded_key = encode_key(k)
            mapping[encoded_key] = encode_element(v, namespace, owner_name, policy)
        namespace._redis().hset(raw_key, mapping=mapping)

    @classmethod
    def read_raw(cls, namespace, raw_key: str) -> dict:
        from ..types import decode_element, decode_key

        data = namespace._redis().hgetall(raw_key)
        if not data:
            return {}
        result = {}
        for encoded_key, encoded_val in data.items():
            key = decode_key(encoded_key)
            result[key] = decode_element(encoded_val, namespace)
        return result

    @classmethod
    def delete_raw(cls, namespace, raw_key: str) -> None:
        namespace._redis().delete(raw_key)


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
        encoded_val = encode_element(value, self._namespace, self._data_owner_name(), policy)
        r.hset(self.raw_key, encoded_key, encoded_val)
        self._release_element_ref(old_encoded)
        self._touch()

    def __delitem__(self, key: object) -> None:
        encoded_key = encode_key(key)
        r = self._redis()
        old_encoded = r.hget(self.raw_key, encoded_key)
        if old_encoded is None:
            raise KeyError(key)
        r.hdel(self.raw_key, encoded_key)
        self._release_element_ref(old_encoded)
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
        value = self._decode_and_copy_value(encoded_val)
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
        owner_name = self._data_owner_name()
        meta = ObjectMeta.load(r, self._canonical_redis_key())
        policy = policy_from_meta(meta) if meta else None
        dirty = False
        try:
            for k, v in pairs:
                encoded_key = encode_key(k)
                old_encoded = r.hget(self.raw_key, encoded_key)
                encoded_val = encode_element(v, self._namespace, owner_name, policy)
                r.hset(self.raw_key, encoded_key, encoded_val)
                self._release_element_ref(old_encoded)
                dirty = True
        finally:
            if dirty:
                self._touch()

    def clear(self):
        encoded_vals = self._redis().hvals(self.raw_key)
        self._redis().delete(self.raw_key)
        for encoded_val in encoded_vals:
            self._release_element_ref(encoded_val)
        self._touch()

    def deepcopy(self):
        return {k: v.deepcopy() if hasattr(v, "deepcopy") else v for k, v in self.value.items()}
