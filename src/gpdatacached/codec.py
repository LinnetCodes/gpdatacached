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
    """Global codec for all scalar types. Stateless; use class methods directly."""

    @classmethod
    def encode_value(cls, value: object) -> str:
        """Encode Python scalar to 'type_name:encoded_value' format."""
        if value is None:
            return "none:None"
        if isinstance(value, bool):
            return f"bool:{value}"
        if isinstance(value, int):
            return f"int:{value}"
        if isinstance(value, float):
            return f"float:{value}"
        if isinstance(value, str):
            return f"str:{value}"
        if isinstance(value, datetime.datetime):
            return f"datetime:{value.isoformat()}"
        if isinstance(value, datetime.date):
            return f"date:{value.isoformat()}"
        if isinstance(value, datetime.time):
            return f"time:{value.isoformat()}"
        raise TypeError(f"Unsupported scalar type: {type(value).__name__!r}")

    # Alias for spec-compatible usage
    encode = encode_value

    @classmethod
    def decode_value(cls, encoded: str) -> object:
        """Decode 'type_name:encoded_value' back to a Python scalar."""
        colon_idx = encoded.index(":")
        type_name = encoded[:colon_idx]
        raw = encoded[colon_idx + 1 :]

        if type_name == "none":
            return None
        if type_name == "bool":
            return raw == "True"
        if type_name == "int":
            return int(raw)
        if type_name == "float":
            return float(raw)
        if type_name == "str":
            return raw
        if type_name == "datetime":
            return datetime.datetime.fromisoformat(raw)
        if type_name == "date":
            return datetime.date.fromisoformat(raw)
        if type_name == "time":
            return datetime.time.fromisoformat(raw)
        raise ValueError(f"Unknown scalar type name: {type_name!r}")

    # Alias for spec-compatible usage
    decode = decode_value

    @classmethod
    def gpdc_type_of(cls, value: object) -> str:
        """Return the GPDC type string (e.g., 'scalar:int') for a Python value."""
        encoded = cls.encode_value(value)
        type_name = encoded[: encoded.index(":")]
        return f"scalar:{type_name}"

    @classmethod
    def encode_tuple(cls, t: tuple) -> str:
        if not t:
            raise ValueError("Cannot encode empty tuple")
        encoded_elems = []
        for elem in t:
            if isinstance(elem, (list, dict, set)):
                raise TypeError(f"Cannot encode non-scalar tuple element: {type(elem).__name__!r}")
            encoded_elems.append(cls.encode_value(elem))
        return "tuple:" + json.dumps(encoded_elems, separators=(",", ":"))

    @classmethod
    def decode_tuple(cls, encoded: str) -> tuple:
        prefix = "tuple:"
        if not encoded.startswith(prefix):
            raise ValueError(f"Not a tuple-encoded string: {encoded!r}")
        json_part = encoded[len(prefix) :]
        encoded_elems = json.loads(json_part)
        return tuple(cls.decode_value(elem) for elem in encoded_elems)

    @classmethod
    def is_tuple_key(cls, encoded: str) -> bool:
        return encoded.startswith("tuple:[")

    @classmethod
    def encode_dict_key(cls, key: object) -> str:
        if isinstance(key, tuple):
            return cls.encode_tuple(key)
        return cls.encode_value(key)

    @classmethod
    def decode_dict_key(cls, encoded: str) -> object:
        if cls.is_tuple_key(encoded):
            return cls.decode_tuple(encoded)
        return cls.decode_value(encoded)


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
        from .types import encode_element

        if not value:
            return
        mapping = {}
        for k, v in value.items():
            encoded_key = GPDCScalarCodec.encode_dict_key(k)
            mapping[encoded_key] = encode_element(v, namespace, owner_name, cache_mode, ttl)
        namespace._redis().hset(raw_key, mapping=mapping)

    @classmethod
    def read_raw(cls, namespace, raw_key: str) -> dict:
        from .types import decode_element

        data = namespace._redis().hgetall(raw_key)
        if not data:
            return {}
        result = {}
        for encoded_key, encoded_val in data.items():
            key = GPDCScalarCodec.decode_dict_key(encoded_key)
            result[key] = decode_element(encoded_val, namespace)
        return result

    @classmethod
    def delete_raw(cls, namespace, raw_key: str) -> None:
        namespace._redis().delete(raw_key)


class SetCodec(GPDCContainerCodec):
    """Codec for Python set values stored as Redis Sets."""

    @classmethod
    def _encode_element(cls, element: object) -> str:
        if isinstance(element, tuple):
            return GPDCScalarCodec.encode_tuple(element)
        return GPDCScalarCodec.encode_value(element)

    @classmethod
    def _decode_element(cls, encoded: str) -> object:
        if encoded.startswith("tuple:["):
            return GPDCScalarCodec.decode_tuple(encoded)
        return GPDCScalarCodec.decode_value(encoded)

    @classmethod
    def create_raw(cls, namespace, raw_key: str, value: set, owner_name: str, cache_mode: str, ttl: int) -> None:
        if not value:
            return
        encoded = [cls._encode_element(elem) for elem in value]
        namespace._redis().sadd(raw_key, *encoded)

    @classmethod
    def read_raw(cls, namespace, raw_key: str) -> set:
        members = namespace._redis().smembers(raw_key)
        if not members:
            return set()
        return {cls._decode_element(m) for m in members}

    @classmethod
    def delete_raw(cls, namespace, raw_key: str) -> None:
        namespace._redis().delete(raw_key)
