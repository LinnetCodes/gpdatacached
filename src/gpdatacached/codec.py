from __future__ import annotations

import datetime
import json
from abc import ABC, abstractmethod


class GPDCCodec(ABC):
    """Abstract base class for all GPDC codecs."""

    @classmethod
    @abstractmethod
    def encode_value(cls, value: object) -> str:
        """Encode a Python value to a string for storage in Redis metadata."""

    @classmethod
    @abstractmethod
    def decode_value(cls, encoded: str) -> object:
        """Decode a stored string back to a Python value."""


class GPDCScalarCodec(GPDCCodec):
    """Abstract base class for all scalar codecs."""

    @classmethod
    def _parse_encoded(cls, encoded: str) -> tuple[str, str]:
        if not encoded or ":" not in encoded:
            raise ValueError(f"Invalid scalar encoding: {encoded!r}")
        colon_idx = encoded.index(":")
        return encoded[:colon_idx], encoded[colon_idx + 1 :]


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


class TupleCodec(GPDCScalarCodec):
    @classmethod
    def encode_value(cls, value: object) -> str:
        if not isinstance(value, tuple):
            raise TypeError(f"TupleCodec cannot encode {type(value).__name__!r}")
        if not value:
            raise ValueError("Cannot encode empty tuple")
        from .types import GPDCTypeRegistry

        encoded_elems = []
        for elem in value:
            if isinstance(elem, (list, dict, set)):
                raise TypeError(f"Cannot encode non-scalar tuple element: {type(elem).__name__!r}")
            entry = GPDCTypeRegistry.lookup_by_python_type(elem)
            encoded_elems.append(entry.codec_class.encode_value(elem))
        return "tuple:" + json.dumps(encoded_elems, separators=(",", ":"))

    @classmethod
    def decode_value(cls, encoded: str) -> object:
        prefix = "tuple:"
        if not encoded.startswith(prefix):
            raise ValueError(f"Not a tuple-encoded string: {encoded!r}")
        json_part = encoded[len(prefix) :]
        encoded_elems = json.loads(json_part)
        from .types import GPDCTypeRegistry

        result = []
        for elem_encoded in encoded_elems:
            if not elem_encoded or ":" not in elem_encoded:
                raise ValueError(f"Invalid tuple element encoding: {elem_encoded!r}")
            colon_idx = elem_encoded.index(":")
            type_name = elem_encoded[:colon_idx]
            gpdc_type = f"scalar:{type_name}"
            entry = GPDCTypeRegistry.lookup_by_gpdc_type(gpdc_type)
            result.append(entry.codec_class.decode_value(elem_encoded))
        return tuple(result)


class GPDCContainerCodec(ABC):
    """Abstract base class for container codecs (list, dict, etc.)."""

    @classmethod
    @abstractmethod
    def create_raw(cls, namespace, raw_key: str, value: object, owner_name: str, cache_mode: str, ttl: int) -> None:
        """Create raw data storage in Redis for the given value."""

    @classmethod
    @abstractmethod
    def read_raw(cls, namespace, raw_key: str) -> object:
        """Restore a Python object from raw data in Redis."""

    @classmethod
    @abstractmethod
    def delete_raw(cls, namespace, raw_key: str) -> None:
        """Delete raw data storage from Redis."""



class ListCodec(GPDCContainerCodec):
    """Codec for Python list values stored as Redis lists."""

    @classmethod
    def create_raw(cls, namespace, raw_key: str, value: list, owner_name: str, cache_mode: str, ttl: int) -> None:
        from .types import encode_element

        if not value:
            return
        encoded = [encode_element(v, namespace, owner_name, cache_mode, ttl) for v in value]
        namespace._redis().rpush(raw_key, *encoded)

    @classmethod
    def read_raw(cls, namespace, raw_key: str) -> list:
        from .types import decode_element

        items = namespace._redis().lrange(raw_key, 0, -1)
        return [decode_element(item, namespace) for item in items]

    @classmethod
    def delete_raw(cls, namespace, raw_key: str) -> None:
        namespace._redis().delete(raw_key)


class DictCodec(GPDCContainerCodec):
    """Codec for Python dict values stored as Redis Hashes."""

    @classmethod
    def create_raw(cls, namespace, raw_key: str, value: dict, owner_name: str, cache_mode: str, ttl: int) -> None:
        from .types import encode_element, encode_key

        if not value:
            return
        mapping = {}
        for k, v in value.items():
            encoded_key = encode_key(k)
            mapping[encoded_key] = encode_element(v, namespace, owner_name, cache_mode, ttl)
        namespace._redis().hset(raw_key, mapping=mapping)

    @classmethod
    def read_raw(cls, namespace, raw_key: str) -> dict:
        from .types import decode_element, decode_key

        data = namespace._redis().hgetall(raw_key)
        if not data:
            return {}
        result = {}
        for encoded_key, encoded_val in data.items():
            key = decode_key(encoded_key)
            result[key] = decode_element(encoded_val, namespace)
        return result

    @classmethod
    def delete_raw(cls, namespace, raw_key: str) -> None:
        namespace._redis().delete(raw_key)


class SetCodec(GPDCContainerCodec):
    """Codec for Python set values stored as Redis Sets."""

    @classmethod
    def create_raw(cls, namespace, raw_key: str, value: set, owner_name: str, cache_mode: str, ttl: int) -> None:
        if not value:
            return
        from .types import encode_key

        encoded = [encode_key(elem) for elem in value]
        namespace._redis().sadd(raw_key, *encoded)

    @classmethod
    def read_raw(cls, namespace, raw_key: str) -> set:
        from .types import decode_key

        members = namespace._redis().smembers(raw_key)
        if not members:
            return set()
        return {decode_key(m) for m in members}

    @classmethod
    def delete_raw(cls, namespace, raw_key: str) -> None:
        namespace._redis().delete(raw_key)
