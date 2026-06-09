from __future__ import annotations

import datetime

from ..codec import GPDCScalarCodec
from ..scalar import GPDCScalarObject


class DateTimeCodec(GPDCScalarCodec):
    @classmethod
    def encode_value(cls, value: object) -> str:
        if not isinstance(value, datetime.datetime):
            raise TypeError(f"DateTimeCodec cannot encode {type(value).__name__!r}")
        return f"datetime:{value.isoformat()}"

    @classmethod
    def decode_value(cls, encoded: str) -> object:
        type_name, raw = cls._parse_encoded(encoded)
        if type_name != "datetime":
            raise ValueError(f"DateTimeCodec cannot decode type {type_name!r}")
        try:
            return datetime.datetime.fromisoformat(raw)
        except (ValueError, TypeError):
            raise ValueError(f"Invalid scalar value for type 'datetime': {raw!r}")


class DateCodec(GPDCScalarCodec):
    @classmethod
    def encode_value(cls, value: object) -> str:
        if isinstance(value, datetime.datetime) or not isinstance(value, datetime.date):
            raise TypeError(f"DateCodec cannot encode {type(value).__name__!r}")
        return f"date:{value.isoformat()}"

    @classmethod
    def decode_value(cls, encoded: str) -> object:
        type_name, raw = cls._parse_encoded(encoded)
        if type_name != "date":
            raise ValueError(f"DateCodec cannot decode type {type_name!r}")
        try:
            return datetime.date.fromisoformat(raw)
        except (ValueError, TypeError):
            raise ValueError(f"Invalid scalar value for type 'date': {raw!r}")


class TimeCodec(GPDCScalarCodec):
    @classmethod
    def encode_value(cls, value: object) -> str:
        if not isinstance(value, datetime.time):
            raise TypeError(f"TimeCodec cannot encode {type(value).__name__!r}")
        return f"time:{value.isoformat()}"

    @classmethod
    def decode_value(cls, encoded: str) -> object:
        type_name, raw = cls._parse_encoded(encoded)
        if type_name != "time":
            raise ValueError(f"TimeCodec cannot decode type {type_name!r}")
        try:
            return datetime.time.fromisoformat(raw)
        except (ValueError, TypeError):
            raise ValueError(f"Invalid scalar value for type 'time': {raw!r}")


class DateTimeObject(GPDCScalarObject):
    TYPE = "scalar:datetime"


class DateObject(GPDCScalarObject):
    TYPE = "scalar:date"


class TimeObject(GPDCScalarObject):
    TYPE = "scalar:time"
