# src/gpdatacached/scalar.py
"""GPDC scalar object hierarchy and type registry."""

from __future__ import annotations

import datetime
from ._base import GPDCDataObject


class GPDCScalarObject(GPDCDataObject):
    """Base class for all scalar GPDC objects."""

    TYPE: str = ""

    @property
    def type(self) -> str:
        return self.__class__.TYPE

    @property
    def value(self) -> object:
        encoded = self._hget("value")
        if not encoded:
            return None
        from .types import GPDCTypeRegistry

        entry = GPDCTypeRegistry.lookup_by_gpdc_type(self.TYPE)
        return entry.codec_class.decode_value(encoded)

    @value.setter
    def value(self, new_value: object) -> None:
        from .types import GPDCTypeRegistry
        from .ttl import policy_from_meta, policy_for_sliding_touch, apply_policy

        entry = GPDCTypeRegistry.lookup_by_gpdc_type(self.TYPE)
        expected_python_type = entry.python_type
        if not isinstance(new_value, expected_python_type):
            raise TypeError(f"Cannot assign {type(new_value).__name__!r} to {self.TYPE}")
        encoded = entry.codec_class.encode_value(new_value)
        key = self._canonical_redis_key()
        now = datetime.datetime.now(datetime.UTC).isoformat()
        r = self._redis()
        r.hset(key, mapping={"value": encoded, "updated_at": now})
        current = policy_from_meta(r.hgetall(key))
        new_policy = policy_for_sliding_touch(current)
        if new_policy is not current:
            apply_policy(r, key, new_policy)


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


class TupleObject(GPDCScalarObject):
    TYPE = "scalar:tuple"


# ── Type registry ─────────────────────────────────────────────────────────────
