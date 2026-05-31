# src/gpdatacached/scalar.py
"""GPDC scalar object hierarchy and type registry."""

from __future__ import annotations

import datetime
from .objects import GPDCDataObject
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
        if meta is None or not meta.value:
            return None
        from .types import GPDCTypeRegistry

        entry = GPDCTypeRegistry.lookup_by_gpdc_type(self.TYPE)
        return entry.codec_class.decode_value(meta.value)

    @value.setter
    def value(self, new_value: object) -> None:
        from .types import GPDCTypeRegistry
        from .ttl import apply_lifecycle, policy_from_meta, policy_for_sliding_touch
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
            return
        meta.with_updates(value=encoded, updated_at=now).save(r, key)
        current = policy_from_meta(meta)
        new_policy = policy_for_sliding_touch(current)
        if new_policy is not current:
            apply_lifecycle(r, key, "", "", False, new_policy)


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


# by design: tuple 注册为 scalar 而非 container。tuple 是不可变的，语义上作为"复合标量"
# （类似数据库复合主键）存储为单个 Redis string，不占用独立的 raw key。TupleCodec 显式
# 拒绝 list/dict/set 元素，避免嵌套引用带来的 GC 复杂性。
class TupleObject(GPDCScalarObject):
    TYPE = "scalar:tuple"


# ── Type registry ─────────────────────────────────────────────────────────────
