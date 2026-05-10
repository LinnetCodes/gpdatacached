from __future__ import annotations

import datetime
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


class GPDCContainerCodec(ABC):
    """Abstract base class for container codecs (list, dict, etc.)."""

    @classmethod
    @abstractmethod
    def create_raw(cls, redis, raw_key: str, value: object) -> None:
        """Create raw data storage in Redis for the given value."""

    @classmethod
    @abstractmethod
    def read_raw(cls, redis, raw_key: str) -> object:
        """Restore a Python object from raw data in Redis."""

    @classmethod
    @abstractmethod
    def delete_raw(cls, redis, raw_key: str) -> None:
        """Delete raw data storage from Redis."""


class ListCodec(GPDCContainerCodec):
    """Codec for Python list values stored as Redis lists."""

    @classmethod
    def create_raw(cls, redis, raw_key: str, value: list) -> None:
        """RPUSH all encoded elements to raw_key. Empty list → nothing pushed."""
        if not value:
            return
        encoded = [GPDCScalarCodec.encode_value(item) for item in value]
        redis.rpush(raw_key, *encoded)

    @classmethod
    def read_raw(cls, redis, raw_key: str) -> list:
        """LRANGE 0 -1 and decode all elements. Missing key returns []."""
        items = redis.lrange(raw_key, 0, -1)
        result = []
        for item in items:
            if item.startswith("ref:"):
                raise NotImplementedError("Reference elements are not supported in this phase")
            result.append(GPDCScalarCodec.decode_value(item))
        return result

    @classmethod
    def delete_raw(cls, redis, raw_key: str) -> None:
        """DEL raw_key."""
        redis.delete(raw_key)
