from __future__ import annotations

import numpy as np
import pandas as pd

from ..container import GPDCContainerObject
from ..codec import GPDCContainerCodec
from ..lifecycle import policy_from_meta
from ..meta import ObjectMeta, _encode_extra_value
from ..types import (
    GPDCTypeRegistry,
    _decode_scalar,
    decode_element,
    encode_element,
    encode_key,
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


class UnsupportedDataFrameError(TypeError):
    """Raised when a DataFrame cannot be stored element-wise by DataFrameObject.

    The message disambiguates the cause (per-column dtype, row index, or
    duplicate column names) and guides the user to register a custom codec.
    """

    _GUIDANCE = (
        "\nSupported dtypes: int64/Int64, float64/Float64, bool/boolean, "
        "str/string, object, datetime64 (naive & tz-aware), category.\n"
        "To cache this, write a custom codec and register it via "
        "GPDCTypeRegistry.register(...)."
    )

    def __init__(self, message: str) -> None:
        super().__init__(message + self._GUIDANCE)


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


def _validate_dataframe(value: pd.DataFrame) -> tuple[list, str]:
    """Validate a DataFrame for storage.

    Returns ``(column_names, index_kind)``. Raises ``UnsupportedDataFrameError``
    on duplicate column names, a MultiIndex row index, an unsupported per-column
    dtype, or an unsupported row-index dtype.
    """
    names = list(value.columns)
    seen: set = set()
    for n in names:
        if n in seen:
            raise UnsupportedDataFrameError(f"Duplicate column names are not supported by DataFrameObject: {names!r}.")
        seen.add(n)
    if isinstance(value.index, pd.MultiIndex):
        raise UnsupportedDataFrameError(
            "DataFrame row index is a MultiIndex, which is not supported by DataFrameObject."
        )
    for col in names:
        if _value_dtype_family(value[col].dtype) is None:
            raise UnsupportedDataFrameError(
                f"Column {col!r} has dtype {value[col].dtype} which is not supported by DataFrameObject."
            )
    kind = _index_kind(value.index)
    if kind is None:
        raise UnsupportedDataFrameError(
            f"DataFrame row index dtype {value.index.dtype} is not supported by DataFrameObject."
        )
    return names, kind


def _python_values(series: pd.Series) -> list:
    """Materialize Series values as Python natives with NA -> None."""
    is_na = pd.isna(series)
    return [None if na else x for x, na in zip(series.tolist(), list(is_na))]


def _python_labels(index) -> list:
    """Materialize Index labels as Python natives with NA -> None."""
    is_na = pd.isna(index)
    return [None if na else x for x, na in zip(index.tolist(), list(is_na))]


def _reconstruct_series(
    values: list,
    index_obj,
    dtype_str: str,
    name,
    namespace,
    cats_ref: str | None = None,
    ordered=None,
) -> pd.Series:
    """Build a pd.Series from decoded values + index + dtype metadata.

    Shared by SeriesCodec.read_raw, DataFrameCodec.read_raw, and DataFrameObject
    column reads. ``cats_ref`` is the encoded ``ref:`` string pointing at a
    categories ListObject (category dtype only).
    """
    family = _value_dtype_family(pd.api.types.pandas_dtype(dtype_str))
    if family == "category":
        cats = decode_element(cats_ref, namespace).value
        cat = pd.Categorical(values, categories=cats, ordered=ordered)
        return pd.Series(cat, index=index_obj, name=name)
    if family == "datetime":
        return pd.Series(values, index=index_obj, name=name).astype(dtype_str)
    return pd.Series(values, index=index_obj, dtype=dtype_str, name=name)


def _reconstruct(values: list, index_obj, fields: dict, namespace) -> pd.Series:
    """Series-specific wrapper over _reconstruct_series (reads Series raw-hash fields)."""
    return _reconstruct_series(
        values,
        index_obj,
        _decode_scalar(fields["dtype"]),
        _decode_scalar(fields["name"]),
        namespace,
        cats_ref=fields.get("categories"),
        ordered=_decode_scalar(fields["ordered"]) if "ordered" in fields else None,
    )


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


def _normalize_extend_input(other) -> tuple[list, list]:
    """Return (index_values, data_values) as Python-native lists from extend input.

    Accepts a pd.Series, a list of pd.Series, or an iterable of (index, value) tuples.
    """
    if isinstance(other, pd.Series):
        return list(other.index), _python_values(other)
    if isinstance(other, (list, tuple)) and len(other) > 0 and isinstance(other[0], pd.Series):
        idx: list = []
        vals: list = []
        for s in other:
            idx.extend(list(s.index))
            vals.extend(_python_values(s))
        return idx, vals
    pairs = list(other)
    bad = [p for p in pairs if not (isinstance(p, tuple) and len(p) == 2)]
    if bad:
        raise TypeError("extend input must be a Series, a list of Series, or (index, value) tuples")
    return [p[0] for p in pairs], [p[1] for p in pairs]


def _is_range_indexed_series(other) -> bool:
    """True when `other` is a Series with a RangeIndex (so a range Series can stay a range)."""
    return isinstance(other, pd.Series) and isinstance(other.index, pd.RangeIndex)


class DataFrameCodec(GPDCContainerCodec):
    """Codec for pandas DataFrame values stored as a Redis hash of column refs."""

    @classmethod
    def create_raw(cls, namespace, raw_key, value: pd.DataFrame, owner_name, cache_mode, ttl) -> None:
        from ..lifecycle import policy_from_defaults

        column_names, kind = _validate_dataframe(value)
        policy = policy_from_defaults(cache_mode, ttl)
        index = value.index

        # Bind all lists to locals BEFORE entering encode_session so they stay
        # alive for the whole session. Passing temporaries inline into
        # encode_element lets CPython reuse a freed id() for the next list,
        # which collides with the encode memo (keyed by id) and aliases
        # distinct content (e.g. an index list silently reuses a column's ref).
        col_values = {col: _python_values(value[col]) for col in column_names}
        col_cats = {
            col: value[col].cat.categories.tolist()
            for col in column_names
            if _value_dtype_family(value[col].dtype) == "category"
        }
        index_labels = _python_labels(index) if kind == "labels" else None

        fields: dict[str, str] = {}
        with encode_session(namespace):
            for col in column_names:
                col_key = encode_key(col)
                fields[col_key] = encode_element(col_values[col], namespace, owner_name, policy)
                if col in col_cats:
                    fields[f"~cat:{col_key}"] = encode_element(col_cats[col], namespace, owner_name, policy)
            if kind == "labels":
                fields["~index"] = encode_element(index_labels, namespace, owner_name, policy)

        for col in column_names:
            col_key = encode_key(col)
            col_series = value[col]
            fields[f"~dt:{col_key}"] = _encode_extra_value(str(col_series.dtype))
            if _value_dtype_family(col_series.dtype) == "category":
                fields[f"~ord:{col_key}"] = _encode_extra_value(bool(col_series.cat.ordered))

        fields["~columns"] = _encode_extra_value(tuple(column_names))
        fields["~index_kind"] = _encode_extra_value(kind)
        fields["~index_name"] = _encode_extra_value(index.name)
        if kind == "labels":
            fields["~index_dtype"] = _encode_extra_value(str(index.dtype))
        else:
            fields["~range_start"] = _encode_extra_value(int(index.start))
            fields["~range_stop"] = _encode_extra_value(int(index.stop))
            fields["~range_step"] = _encode_extra_value(int(index.step))

        namespace._redis().hset(raw_key, mapping=fields)

    @classmethod
    def read_raw(cls, namespace, raw_key) -> pd.DataFrame:
        raise NotImplementedError  # implemented in Task 4

    @classmethod
    def delete_raw(cls, namespace, raw_key) -> None:
        namespace._redis().delete(raw_key)


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
        values = data.deepcopy()
        index_obj = _build_index(fields, namespace)
        return _reconstruct(values, index_obj, fields, namespace)

    @classmethod
    def delete_raw(cls, namespace, raw_key) -> None:
        namespace._redis().delete(raw_key)


_UNLOADED = object()


class _ILocIndexer:
    def __init__(self, owner: "SeriesObject") -> None:
        self._owner = owner

    def __getitem__(self, key):
        owner = self._owner
        data = owner._get_data_list()
        if data is None:
            raise IndexError("SeriesObject has no data")
        if isinstance(key, (int, np.integer)):
            return data[int(key)]
        if isinstance(key, slice):
            values = data[key]
            return owner._build_series_from(values, owner._positional_index_slice(key))
        raise TypeError(f"iloc expects int or slice, got {type(key).__name__!r}")


class _LocIndexer:
    def __init__(self, owner: "SeriesObject") -> None:
        self._owner = owner

    def __getitem__(self, label):
        owner = self._owner
        index_list = owner._get_index_list()
        if index_list is None:
            raise TypeError(".loc is not supported on a RangeIndex Series; use .iloc")
        labels = index_list.value
        positions = [i for i, lbl in enumerate(labels) if lbl == label]
        if not positions:
            raise KeyError(label)
        data = owner._get_data_list()
        values = [data[p] for p in positions]
        index_name = owner.index_name
        return owner._build_series_from(values, pd.Index([labels[p] for p in positions], name=index_name))


class SeriesObject(GPDCContainerObject):
    TYPE = "collection:pandas_series"

    def __init__(self, name, namespace, *, canonical_name=None) -> None:
        super().__init__(name, namespace, canonical_name=canonical_name)
        self._data_list_cache = _UNLOADED
        self._index_list_cache = _UNLOADED

    @property
    def type(self) -> str:
        return self.TYPE

    @property
    def value(self) -> pd.Series:
        return SeriesCodec.read_raw(self._namespace, self.raw_key)

    @value.setter
    def value(self, new_value) -> None:
        self._reject_value_replacement()

    def _get_data_list(self):
        if self._data_list_cache is _UNLOADED:
            ref = self._redis().hget(self.raw_key, "data")
            self._data_list_cache = decode_element(ref, self._namespace) if ref else None
        return self._data_list_cache

    def _get_index_list(self):
        """Return the index child ListObject, or None when index_kind is 'range'."""
        if self._index_list_cache is _UNLOADED:
            ref = self._redis().hget(self.raw_key, "index")
            self._index_list_cache = decode_element(ref, self._namespace) if ref else None
        return self._index_list_cache

    def _raw_fields(self) -> dict:
        return self._redis().hgetall(self.raw_key)

    def __len__(self) -> int:
        data = self._get_data_list()
        return len(data) if data is not None else 0

    @property
    def dtype(self) -> str:
        return _decode_scalar(self._redis().hget(self.raw_key, "dtype"))

    @property
    def name(self):
        return _decode_scalar(self._redis().hget(self.raw_key, "name"))

    @property
    def index_name(self):
        return _decode_scalar(self._redis().hget(self.raw_key, "index_name"))

    def deepcopy(self) -> pd.Series:
        return self.value.copy(deep=True)

    def _current_item_count(self) -> int:
        return len(self)

    @property
    def iloc(self) -> "_ILocIndexer":
        return _ILocIndexer(self)

    @property
    def loc(self) -> "_LocIndexer":
        return _LocIndexer(self)

    def _positional_index_slice(self, sl: slice):
        """Return the pd.Index for a positional slice, honoring RangeIndex."""
        fields = self._raw_fields()
        index_kind = _decode_scalar(fields["index_kind"])
        index_name = _decode_scalar(fields["index_name"])
        if index_kind == "range":
            start = _decode_scalar(fields["range_start"])
            stop = _decode_scalar(fields["range_stop"])
            step = _decode_scalar(fields["range_step"])
            full = pd.RangeIndex(start, stop, step, name=index_name)
            return full[sl]
        index_list = self._get_index_list()
        labels = index_list[sl] if index_list is not None else []
        return pd.Index(labels, name=index_name)

    def _build_series_from(self, values: list, index_obj) -> pd.Series:
        return _reconstruct(values, index_obj, self._raw_fields(), self._namespace)

    def extend(self, other) -> None:
        """Append rows in place. Accepts a Series, a list of Series, or (index, value) tuples.

        Mutates the cached object in Redis and returns None. (Differs from
        pandas pd.concat, which is non-mutating and returns a new Series.)
        """
        from ..lifecycle import policy_from_defaults

        fields = self._raw_fields()
        dtype_str = _decode_scalar(fields["dtype"])
        if isinstance(pd.api.types.pandas_dtype(dtype_str), pd.CategoricalDtype):
            raise TypeError(
                "Extending a categorical-dtype Series is not supported; "
                "create a new Series with the desired categories instead."
            )
        index_values, data_values = _normalize_extend_input(other)

        try:
            casted = pd.Series(data_values, dtype=dtype_str)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Cannot extend Series of dtype {dtype_str!r} with given values: {exc}") from exc
        new_data = _python_values(casted)

        r = self._redis()
        meta = ObjectMeta.load(r, self._canonical_redis_key())
        policy = policy_from_meta(meta) if meta else policy_from_defaults("permanent", 0)
        owner_name = self._data_owner_name()

        index_kind = _decode_scalar(fields["index_kind"])
        if index_kind == "range":
            needs_promote = not _is_range_indexed_series(other)
            if needs_promote:
                self._promote_range_to_labels(fields, owner_name, policy)
                index_kind = "labels"

        data_list = self._get_data_list()
        index_list = self._get_index_list()

        pipe = r.pipeline(transaction=True)
        with encode_session(self._namespace):
            encoded_data = [encode_element(v, self._namespace, owner_name, policy) for v in new_data]
        if encoded_data:
            pipe.rpush(data_list.raw_key, *encoded_data)
        if index_kind == "labels":
            with encode_session(self._namespace):
                encoded_idx = [encode_element(v, self._namespace, owner_name, policy) for v in index_values]
            if encoded_idx:
                pipe.rpush(index_list.raw_key, *encoded_idx)
        else:
            new_stop = _decode_scalar(fields["range_stop"]) + len(new_data)
            pipe.hset(self.raw_key, "range_stop", _encode_extra_value(new_stop))
        pipe.execute()
        self._data_list_cache = _UNLOADED
        self._index_list_cache = _UNLOADED
        self._touch()

    def _promote_range_to_labels(self, fields: dict, owner_name, policy) -> None:
        start = _decode_scalar(fields["range_start"])
        stop = _decode_scalar(fields["range_stop"])
        step = _decode_scalar(fields["range_step"])
        labels = list(pd.RangeIndex(start, stop, step))
        with encode_session(self._namespace):
            ref = encode_element(labels, self._namespace, owner_name, policy)
        r = self._redis()
        mapping = {
            "index": ref,
            "index_kind": _encode_extra_value("labels"),
            "index_dtype": _encode_extra_value("int64"),
        }
        for k in ("range_start", "range_stop", "range_step"):
            if k in fields:
                r.hdel(self.raw_key, k)
        r.hset(self.raw_key, mapping=mapping)
        self._index_list_cache = _UNLOADED
