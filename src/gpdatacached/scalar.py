# src/gpdatacached/scalar.py
"""GPDC scalar object hierarchy and type registry."""

from __future__ import annotations

import datetime
from .exceptions import GPDCError, ObjectGoneError
from .object import GPDCDataObject
from .meta import ObjectMeta


class GPDCScalarObject(GPDCDataObject):
    """Base class for all scalar GPDC objects."""

    TYPE: str = ""

    @property
    def type(self) -> str:
        return self.__class__.TYPE

    @property
    def value(self) -> object:
        key = self._canonical_redis_key()
        meta = ObjectMeta.load(self._redis(), key)
        if meta is None:
            raise ObjectGoneError(self.canonical_uri)
        if self.TYPE == "scalar:none":
            return None
        if not meta.value:
            raise GPDCError(f"Corrupted scalar object {self.canonical_uri!r}: empty value field")
        from .types import GPDCTypeRegistry

        entry = GPDCTypeRegistry.lookup_by_gpdc_type(self.TYPE)
        return entry.codec_class.decode_value(meta.value)

    @value.setter
    def value(self, new_value: object) -> None:
        from .types import GPDCTypeRegistry
        from .lifecycle import policy_from_meta
        from .meta import ObjectMeta

        entry = GPDCTypeRegistry.lookup_by_gpdc_type(self.TYPE)
        expected_python_type = entry.python_type
        if not isinstance(new_value, expected_python_type):
            raise TypeError(f"Cannot assign {type(new_value).__name__!r} to {self.TYPE}")
        encoded = entry.codec_class.encode_value(new_value)
        key = self._canonical_redis_key()
        now = datetime.datetime.now(datetime.UTC)
        r = self._redis()
        meta = ObjectMeta.load(r, key)
        if meta is None:
            raise ObjectGoneError(self.canonical_uri)
        meta.with_updates(value=encoded, updated_at=now).save(r, key)
        policy = policy_from_meta(meta)
        if policy.cache_mode == "sliding" and policy.declared_ttl > 0:
            self.refresh_ttl()
