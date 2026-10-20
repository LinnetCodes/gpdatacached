from __future__ import annotations

import json

from ..codec import GPDCScalarCodec
from ..scalar import GPDCScalarObject


class TupleCodec(GPDCScalarCodec):
    @classmethod
    def encode_value(cls, value: object) -> str:
        if not isinstance(value, tuple):
            raise TypeError(f"TupleCodec cannot encode {type(value).__name__!r}")
        from ..types import GPDCTypeRegistry

        encoded_elems = []
        for elem in value:
            if isinstance(elem, (list, dict, set)):
                raise TypeError(f"Cannot encode non-scalar tuple element: {type(elem).__name__!r}")
            entry = GPDCTypeRegistry.lookup_by_python_type(elem)
            encoded_elems.append(entry.scalar_codec_class.encode_value(elem))
        return "tuple:" + json.dumps(encoded_elems, separators=(",", ":"))

    @classmethod
    def decode_value(cls, encoded: str) -> object:
        prefix = "tuple:"
        if not encoded.startswith(prefix):
            raise ValueError(f"Not a tuple-encoded string: {encoded!r}")
        json_part = encoded[len(prefix) :]
        encoded_elems = json.loads(json_part)
        from ..types import GPDCTypeRegistry

        result = []
        for elem_encoded in encoded_elems:
            if not elem_encoded or ":" not in elem_encoded:
                raise ValueError(f"Invalid tuple element encoding: {elem_encoded!r}")
            colon_idx = elem_encoded.index(":")
            type_name = elem_encoded[:colon_idx]
            gpdc_type = f"scalar:{type_name}"
            entry = GPDCTypeRegistry.lookup_by_gpdc_type(gpdc_type)
            result.append(entry.scalar_codec_class.decode_value(elem_encoded))
        return tuple(result)


# by design: tuple 注册为 scalar 而非 container。tuple 是不可变的，语义上作为"复合标量"
# （类似数据库复合主键）存储为单个 Redis string，不占用独立的 raw key。TupleCodec 显式
# 拒绝 list/dict/set 元素，避免嵌套引用带来的 GC 复杂性。
class TupleObject(GPDCScalarObject):
    TYPE = "scalar:tuple"
