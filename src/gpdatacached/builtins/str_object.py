from __future__ import annotations

from ..codec import GPDCScalarCodec
from ..scalar import GPDCScalarObject


class StrCodec(GPDCScalarCodec):
    @classmethod
    def encode_value(cls, value: object) -> str:
        if not isinstance(value, str):
            raise TypeError(f"StrCodec cannot encode {type(value).__name__!r}")
        return f"str:{value}"

    @classmethod
    def decode_value(cls, encoded: str) -> object:
        type_name, _ = cls._parse_encoded(encoded)
        if type_name != "str":
            raise ValueError(f"StrCodec cannot decode type {type_name!r}")
        return encoded[encoded.index(":") + 1 :]


class StrObject(GPDCScalarObject):
    TYPE = "scalar:str"
