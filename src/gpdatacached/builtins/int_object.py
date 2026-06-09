from __future__ import annotations

from ..codec import GPDCScalarCodec
from ..scalar import GPDCScalarObject


class IntCodec(GPDCScalarCodec):
    @classmethod
    def encode_value(cls, value: object) -> str:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"IntCodec cannot encode {type(value).__name__!r}")
        return f"int:{value}"

    @classmethod
    def decode_value(cls, encoded: str) -> object:
        type_name, raw = cls._parse_encoded(encoded)
        if type_name != "int":
            raise ValueError(f"IntCodec cannot decode type {type_name!r}")
        try:
            return int(raw)
        except (ValueError, OverflowError):
            raise ValueError(f"Invalid scalar value for type 'int': {raw!r}")


class IntObject(GPDCScalarObject):
    TYPE = "scalar:int"
