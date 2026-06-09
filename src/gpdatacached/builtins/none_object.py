from __future__ import annotations

from ..codec import GPDCScalarCodec
from ..scalar import GPDCScalarObject


class NoneCodec(GPDCScalarCodec):
    @classmethod
    def encode_value(cls, value: object) -> str:
        if value is not None:
            raise TypeError(f"NoneCodec cannot encode {type(value).__name__!r}")
        return "none:None"

    @classmethod
    def decode_value(cls, encoded: str) -> object:
        type_name, raw = cls._parse_encoded(encoded)
        if type_name != "none":
            raise ValueError(f"NoneCodec cannot decode type {type_name!r}")
        if raw != "None":
            raise ValueError(f"Invalid scalar value for type 'none': {raw!r}")
        return None


class NoneObject(GPDCScalarObject):
    TYPE = "scalar:none"
