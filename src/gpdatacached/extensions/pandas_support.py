from __future__ import annotations

import pandas as pd

from ..container import GPDCContainerObject
from ..codec import GPDCContainerCodec
from ..meta import _encode_extra_value
from ..types import (
    GPDCTypeRegistry,
    _decode_scalar,
    decode_element,
    encode_element,
    encode_session,
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


def _python_values(series: pd.Series) -> list:
    """Materialize Series values as Python natives with NA -> None."""
    is_na = pd.isna(series)
    return [None if na else x for x, na in zip(series.tolist(), list(is_na))]


def _python_labels(index) -> list:
    """Materialize Index labels as Python natives with NA -> None."""
    is_na = pd.isna(index)
    return [None if na else x for x, na in zip(index.tolist(), list(is_na))]


def _reconstruct(values: list, index_obj, fields: dict, namespace) -> pd.Series:
    """Build a pd.Series from decoded values + index + raw-hash scalar fields."""
    dtype_str = _decode_scalar(fields["dtype"])
    name = _decode_scalar(fields["name"])
    family = _value_dtype_family(pd.api.types.pandas_dtype(dtype_str))
    if family == "category":
        cats = decode_element(fields["categories"], namespace).value
        ordered = _decode_scalar(fields["ordered"])
        cat = pd.Categorical(values, categories=cats, ordered=ordered)
        return pd.Series(cat, index=index_obj, name=name)
    if family == "datetime":
        return pd.Series(values, index=index_obj, name=name).astype(dtype_str)
    return pd.Series(values, index=index_obj, dtype=dtype_str, name=name)


def _build_index(fields: dict, namespace) -> "pd.Index":
    index_kind = _decode_scalar(fields["index_kind"])
    index_name = _decode_scalar(fields["index_name"])
    if index_kind == "range":
        start = _decode_scalar(fields["range_start"])
        stop = _decode_scalar(fields["range_stop"])
        step = _decode_scalar(fields["range_step"])
        return pd.RangeIndex(start, stop, step, name=index_name)
    labels = decode_element(fields["index"], namespace).value
    return pd.Index(labels, name=index_name)


def register() -> None:
    """Register the pandas Series type with GPDCTypeRegistry (idempotent)."""
    GPDCTypeRegistry.register(
        python_type=pd.Series,
        gpdc_type="collection:pandas_series",
        object_class=SeriesObject,
        codec_class=SeriesCodec,
        priority=0,
    )


class SeriesCodec(GPDCContainerCodec):
    @classmethod
    def create_raw(cls, namespace, raw_key, value: pd.Series, owner_name, cache_mode, ttl) -> None:
        from ..lifecycle import policy_from_defaults

        family = _value_dtype_family(value.dtype)
        if family is None:
            raise UnsupportedSeriesDtypeError(str(value.dtype), is_index=False)
        index = value.index
        kind = _index_kind(index)
        if kind is None:
            raise UnsupportedSeriesDtypeError(str(index.dtype), is_index=True)

        policy = policy_from_defaults(cache_mode, ttl)
        data_values = _python_values(value)
        index_labels = _python_labels(index) if kind == "labels" else None
        categories = value.cat.categories.tolist() if family == "category" else None
        fields: dict[str, str] = {}
        with encode_session(namespace):
            fields["data"] = encode_element(data_values, namespace, owner_name, policy)
            if kind == "labels":
                fields["index"] = encode_element(index_labels, namespace, owner_name, policy)
            if family == "category":
                fields["categories"] = encode_element(categories, namespace, owner_name, policy)

        fields["dtype"] = _encode_extra_value(str(value.dtype))
        fields["name"] = _encode_extra_value(value.name)
        fields["index_kind"] = _encode_extra_value(kind)
        fields["index_name"] = _encode_extra_value(index.name)
        if kind == "labels":
            fields["index_dtype"] = _encode_extra_value(str(index.dtype))
        if family == "category":
            fields["ordered"] = _encode_extra_value(bool(value.cat.ordered))
        if kind == "range":
            fields["range_start"] = _encode_extra_value(int(index.start))
            fields["range_stop"] = _encode_extra_value(int(index.stop))
            fields["range_step"] = _encode_extra_value(int(index.step))

        namespace._redis().hset(raw_key, mapping=fields)

    @classmethod
    def read_raw(cls, namespace, raw_key) -> pd.Series:
        fields = namespace._redis().hgetall(raw_key)
        if not fields:
            return pd.Series([], dtype="object")
        data = decode_element(fields["data"], namespace)
        # Fully materialize nested GPDC containers (dict/list) into plain Python
        # values so object-dtype Series round-trip as native dict/list, not as
        # DictObject/ListObject wrappers.
        values = data.deepcopy() if hasattr(data, "deepcopy") else data.value
        index_obj = _build_index(fields, namespace)
        return _reconstruct(values, index_obj, fields, namespace)

    @classmethod
    def delete_raw(cls, namespace, raw_key) -> None:
        namespace._redis().delete(raw_key)


# Forward-declared placeholder; implemented in later tasks.
class SeriesObject(GPDCContainerObject):
    TYPE = "collection:pandas_series"
