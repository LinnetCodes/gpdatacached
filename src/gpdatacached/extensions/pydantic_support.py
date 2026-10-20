from __future__ import annotations

import collections.abc
import json
from typing import Any, cast

from ..codec import GPDCContainerCodec, GPDCScalarCodec
from ..container import GPDCContainerObject
from ..types import decode_element, encode_element


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
            encoded_fields[field_name] = entry.scalar_codec_class.encode_value(field_value)

        type_name = _gpdc_type_to_prefix(value)
        return f"{type_name}:{json.dumps(encoded_fields, separators=(',', ':'))}"

    @classmethod
    def decode_value(cls, encoded: str) -> Any:
        type_name, json_part = cls._parse_encoded(encoded)
        from ..types import GPDCTypeRegistry, _decode_scalar

        gpdc_type = f"scalar:{type_name}"
        entry = GPDCTypeRegistry.lookup_by_gpdc_type(gpdc_type)
        # Scalar entries register a single python type (tuples are only used
        # for multi-type registrations like int/float str aliases).
        model_class = cast("type", entry.python_type)

        raw_fields = json.loads(json_part)
        decoded_fields = {}
        for field_name, field_encoded in raw_fields.items():
            decoded_fields[field_name] = _decode_scalar(field_encoded)

        return model_class(**decoded_fields)


def _gpdc_type_to_prefix(model_instance) -> str:
    from ..types import GPDCTypeRegistry

    entry = GPDCTypeRegistry.lookup_by_python_type(model_instance)
    return entry.gpdc_type.split(":", 1)[1]


class PydanticModelContainerCodec(GPDCContainerCodec):
    @classmethod
    def create_raw(cls, namespace, raw_key: str, value: object, owner_name: str, cache_mode: str, ttl: int) -> None:
        from pydantic import BaseModel
        from ..lifecycle import policy_from_defaults

        if not isinstance(value, BaseModel):
            raise TypeError(f"Expected BaseModel, got {type(value).__name__!r}")

        policy = policy_from_defaults(cache_mode, ttl)
        mapping = {}
        for field_name in type(value).model_fields:
            field_value = getattr(value, field_name)
            mapping[field_name] = encode_element(field_value, namespace, owner_name, policy)
        if mapping:
            namespace._redis().hset(raw_key, mapping=mapping)

    @classmethod
    def read_raw(cls, namespace, raw_key: str) -> dict:
        data = namespace._redis().hgetall(raw_key)
        if not data:
            return {}
        result = {}
        for field_name, encoded_val in data.items():
            result[field_name] = decode_element(encoded_val, namespace)
        return result

    @classmethod
    def delete_raw(cls, namespace, raw_key: str) -> None:
        namespace._redis().delete(raw_key)


class BaseModelContainerObject(GPDCContainerObject, collections.abc.MutableMapping):
    @property
    def type(self) -> str:
        return self.__class__.TYPE

    @property
    def _model_class(self):
        from ..types import GPDCTypeRegistry

        return GPDCTypeRegistry.lookup_by_gpdc_type(self.TYPE).python_type

    @property
    def _field_names(self) -> frozenset[str]:
        return frozenset(self._model_class.model_fields.keys())

    @property
    def value(self):
        raw_dict = PydanticModelContainerCodec.read_raw(self._namespace, self.raw_key)
        return self._model_class(**raw_dict)

    @value.setter
    def value(self, new_value):
        self._reject_value_replacement()

    def _current_item_count(self) -> int:
        return len(self._field_names)

    def __getitem__(self, key: str):
        if key not in self._field_names:
            raise KeyError(key)
        encoded_val = self._redis().hget(self.raw_key, key)
        if encoded_val is None:
            # Missing slot (model evolution, external ops, GC edge cases): fall back to
            # the field's declared default. Use is_required() rather than checking
            # field.default — pydantic stores "no default" as the PydanticUndefined
            # sentinel (which is not None), so a naive `default is not None` test would
            # both miss `default=None` fields and leak the sentinel for factory/required
            # fields.
            field = self._model_class.model_fields[key]
            if field.is_required():
                raise KeyError(key)
            if field.default_factory is not None:
                return field.default_factory()
            return field.default
        return decode_element(encoded_val, self._namespace)

    def __setitem__(self, key: str, value: object) -> None:
        if key not in self._field_names:
            raise KeyError(f"Cannot set unknown field {key!r} on {self._model_class.__name__}")
        # Validate the value against the field's declared type BEFORE any Redis
        # write, so a rejected value leaves the object untouched (CQR-2026-07-24
        # behavior.01). This is single-field validation only — it does NOT run
        # cross-field / model-level validators. That is intentional: container
        # mode trades whole-model validation for field-level access; users who
        # need cross-field validation should register their model with the
        # scalar codec (PydanticModelScalarCodec), which stores and validates
        # the model atomically.
        self._validate_field_value(key, value)
        from ..lifecycle import policy_from_meta

        r = self._redis()
        old_encoded = r.hget(self.raw_key, key)
        # Liveness check before any Redis write (logic.02); meta reused for policy + _touch.
        meta = self._check_alive(r)
        policy = policy_from_meta(meta)
        encoded_val = encode_element(value, self._namespace, self._data_owner_name(), policy)
        r.hset(self.raw_key, key, encoded_val)
        self._release_element_ref(old_encoded)
        self._touch(meta)

    def _validate_field_value(self, key: str, value: object) -> None:
        """Validate ``value`` against the declared type of field ``key``.

        Raises ``pydantic.ValidationError`` on a type mismatch (e.g. a string
        into a ``float`` field). Uses the model class's own field annotation
        with pydantic's default (lenient, coercing) config, so it accepts the
        same coercions a normal ``Model(field=value)`` construction would.
        Cross-field validators are NOT evaluated here (see __setitem__ docstring).
        """
        from pydantic import TypeAdapter

        field = self._model_class.model_fields[key]
        TypeAdapter(field.annotation).validate_python(value)

    def __delitem__(self, key: str) -> None:
        raise TypeError("Cannot delete fields from a Pydantic model container")

    def __contains__(self, key: object) -> bool:
        return key in self._field_names

    def __iter__(self):
        return iter(self._field_names)

    def __len__(self) -> int:
        return len(self._field_names)

    def deepcopy(self):
        raw = PydanticModelContainerCodec.read_raw(self._namespace, self.raw_key)
        return self._model_class(**{k: v.deepcopy() if hasattr(v, "deepcopy") else v for k, v in raw.items()})
