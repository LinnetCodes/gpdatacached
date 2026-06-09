from __future__ import annotations

from ..codec import GPDCScalarCodec
from ..scalar import GPDCScalarObject


class BoolCodec(GPDCScalarCodec):
    @classmethod
    def encode_value(cls, value: object) -> str:
        if not isinstance(value, bool):
            raise TypeError(f"BoolCodec cannot encode {type(value).__name__!r}")
        return f"bool:{value}"

    @classmethod
    def decode_value(cls, encoded: str) -> object:
        type_name, raw = cls._parse_encoded(encoded)
        if type_name != "bool":
            raise ValueError(f"BoolCodec cannot decode type {type_name!r}")
        if raw not in ("True", "False"):
            raise ValueError(f"Invalid scalar value for type 'bool': {raw!r}")
        return raw == "True"


class BoolObject(GPDCScalarObject):
    TYPE = "scalar:bool"
