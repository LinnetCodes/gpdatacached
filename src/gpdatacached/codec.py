from __future__ import annotations

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
