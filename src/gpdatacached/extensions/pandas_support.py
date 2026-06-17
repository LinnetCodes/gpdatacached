from __future__ import annotations

import pandas as pd

from ..container import GPDCContainerObject
from ..codec import GPDCContainerCodec
from ..meta import _encode_extra_value  # noqa: F401  (used in later tasks)
from ..types import (
    GPDCTypeRegistry,
    _decode_scalar,  # noqa: F401  (used in later tasks)
    decode_element,  # noqa: F401  (used in later tasks)
    encode_element,  # noqa: F401  (used in later tasks)
    encode_session,  # noqa: F401  (used in later tasks)
)

__all__ = [
    "SeriesObject",
    "SeriesCodec",
    "UnsupportedSeriesDtypeError",
    "register",
]


class UnsupportedSeriesDtypeError(TypeError):
    """Raised when a pandas dtype cannot be stored element-wise by SeriesObject.

    Guides the user to register a custom codec for the dtype.
    """

    def __init__(self, dtype: str, *, is_index: bool) -> None:
        target = "index dtype" if is_index else "Series dtype"
        super().__init__(
            f"{target} {dtype!r} is not supported by SeriesObject.\n"
            "Supported dtypes: int64/Int64, float64/Float64, bool/boolean, "
            "str/string, object, datetime64 (naive & tz-aware), category.\n"
            "To cache this dtype, write a custom codec and register it via "
            "GPDCTypeRegistry.register(...)."
        )


def _value_dtype_family(dtype) -> str | None:
    """Classify a pandas value dtype into a reconstruction family, or None if unsupported."""
    if isinstance(dtype, pd.CategoricalDtype):
        return "category"
    if isinstance(dtype, pd.SparseDtype):
        return None
    if pd.api.types.is_datetime64_any_dtype(dtype):
        return "datetime"
    if pd.api.types.is_bool_dtype(dtype):
        return "bool"
    if pd.api.types.is_integer_dtype(dtype):
        return "integer"
    if pd.api.types.is_float_dtype(dtype):
        return "float"
    string_dtype = getattr(pd, "StringDtype", None)
    if (string_dtype is not None and isinstance(dtype, string_dtype)) or dtype.kind in ("O", "U", "S"):
        return "object_or_string"
    return None


def _index_kind(index) -> str | None:
    """Classify a pandas index: 'range', 'labels', or None if unsupported."""
    if isinstance(index, pd.MultiIndex):
        return None
    if isinstance(index, pd.RangeIndex):
        return "range"
    return "labels" if _value_dtype_family(index.dtype) is not None else None


def register() -> None:
    """Register the pandas Series type with GPDCTypeRegistry (idempotent)."""
    GPDCTypeRegistry.register(
        python_type=pd.Series,
        gpdc_type="collection:pandas_series",
        object_class=SeriesObject,
        codec_class=SeriesCodec,
        priority=0,
    )


# Forward-declared placeholders; implemented in later tasks.
class SeriesCodec(GPDCContainerCodec):
    @classmethod
    def create_raw(cls, namespace, raw_key, value, owner_name, cache_mode, ttl):  # pragma: no cover
        raise NotImplementedError

    @classmethod
    def read_raw(cls, namespace, raw_key):  # pragma: no cover
        raise NotImplementedError

    @classmethod
    def delete_raw(cls, namespace, raw_key):  # pragma: no cover
        raise NotImplementedError


class SeriesObject(GPDCContainerObject):
    TYPE = "collection:pandas_series"
