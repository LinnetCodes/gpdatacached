from __future__ import annotations

import json
from typing import Any

from ..codec import GPDCScalarCodec


class PydanticModelScalarCodec(GPDCScalarCodec):
    @classmethod
    def encode_value(cls, value: object) -> str:
        from pydantic import BaseModel

        if not isinstance(value, BaseModel):
            raise TypeError(
                f"PydanticModelScalarCodec cannot encode {type(value).__name__!r}; expected a BaseModel subclass"
            )

        from ..types import GPDCTypeRegistry

        encoded_fields: dict[str, str] = {}
        for field_name in type(value).model_fields:
            field_value = getattr(value, field_name)
            entry = GPDCTypeRegistry.lookup_by_python_type(field_value)
            if entry.is_container:
                raise TypeError(
                    f"PydanticModelScalarCodec does not support container field type "
                    f"{type(field_value).__name__!r} in field {field_name!r}"
                )
            encoded_fields[field_name] = entry.codec_class.encode_value(field_value)

        type_name = _gpdc_type_to_prefix(value)
        return f"{type_name}:{json.dumps(encoded_fields, separators=(',', ':'))}"

    @classmethod
    def decode_value(cls, encoded: str) -> Any:
        type_name, json_part = cls._parse_encoded(encoded)
        from ..types import GPDCTypeRegistry, _decode_scalar

        gpdc_type = f"scalar:{type_name}"
        entry = GPDCTypeRegistry.lookup_by_gpdc_type(gpdc_type)
        model_class = entry.python_type

        raw_fields = json.loads(json_part)
        decoded_fields = {}
        for field_name, field_encoded in raw_fields.items():
            decoded_fields[field_name] = _decode_scalar(field_encoded)

        return model_class(**decoded_fields)


def _gpdc_type_to_prefix(model_instance) -> str:
    from ..types import GPDCTypeRegistry

    entry = GPDCTypeRegistry.lookup_by_python_type(model_instance)
    return entry.gpdc_type.split(":", 1)[1]
