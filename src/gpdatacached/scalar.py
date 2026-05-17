# src/gpdatacached/scalar.py
"""GPDC scalar object hierarchy and type registry."""

from __future__ import annotations

import datetime
from .codec import GPDCScalarCodec
from .objects import GPDCDataObject


class GPDCScalarObject(GPDCDataObject):
    """Base class for all scalar GPDC objects."""

    TYPE: str = ""

    @property
    def type(self) -> str:
        return self.__class__.TYPE

    @property
    def value(self) -> object:
        """Read and decode the stored scalar value from Redis."""
        encoded = self._hget("value")
        if not encoded:
            return None
        return GPDCScalarCodec.decode_value(encoded)

    @value.setter
    def value(self, new_value: object) -> None:
        """Encode and persist a new scalar value; update updated_at and slide TTL."""
        encoded = GPDCScalarCodec.encode_value(new_value)
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
