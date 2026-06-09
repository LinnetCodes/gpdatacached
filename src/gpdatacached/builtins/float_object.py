from __future__ import annotations

from ..codec import GPDCScalarCodec
from ..scalar import GPDCScalarObject


class FloatCodec(GPDCScalarCodec):
    @classmethod
    def encode_value(cls, value: object) -> str:
        if not isinstance(value, float):
            raise TypeError(f"FloatCodec cannot encode {type(value).__name__!r}")
        return f"float:{value}"

    @classmethod
    def decode_value(cls, encoded: str) -> object:
        type_name, raw = cls._parse_encoded(encoded)
        if type_name != "float":
            raise ValueError(f"FloatCodec cannot decode type {type_name!r}")
        try:
            return float(raw)
        except ValueError:
            raise ValueError(f"Invalid scalar value for type 'float': {raw!r}")


class FloatObject(GPDCScalarObject):
    TYPE = "scalar:float"
