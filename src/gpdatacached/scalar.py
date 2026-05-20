# src/gpdatacached/scalar.py
"""GPDC scalar object hierarchy and type registry."""

from __future__ import annotations

import datetime
from .codec import GPDCScalarCodec
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

        try:
            entry = GPDCTypeRegistry.lookup_by_gpdc_type(self.TYPE)
            codec = entry.codec_class
            if codec is not None:
                return codec.decode_value(encoded)
        except (ValueError, KeyError):
            pass
        return GPDCScalarCodec.decode_value(encoded)

    @value.setter
    def value(self, new_value: object) -> None:
        from .types import GPDCTypeRegistry

        entry = GPDCTypeRegistry.lookup_by_gpdc_type(self.TYPE)
        expected_python_type = entry.python_type
        if not isinstance(new_value, expected_python_type):
            raise TypeError(f"Cannot assign {type(new_value).__name__!r} to {self.TYPE}")
        codec = entry.codec_class or GPDCScalarCodec
        encoded = codec.encode_value(new_value)
        key = self._redis_key()
        now = datetime.datetime.now(datetime.UTC).isoformat()
        r = self._redis()
        r.hset(key, mapping={"value": encoded, "updated_at": now})
        if self.cache_mode == "sliding":
            ttl = self.ttl
            if ttl > 0:
                r.expire(key, ttl)


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


# ── Type registry ─────────────────────────────────────────────────────────────
