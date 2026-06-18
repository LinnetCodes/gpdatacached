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
    "DataFrameObject",
    "DataFrameCodec",
    "UnsupportedDataFrameError",
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


def _build_index(fields: dict, namespace, prefix: str = "") -> "pd.Index":
    """Build a pd.Index from raw-hash scalar/index fields.

    ``prefix`` is "" for Series raw-hash field names (``index_kind``) and "~"
    for DataFrame raw-hash field names (``~index_kind``).
    """
    index_kind = _decode_scalar(fields[f"{prefix}index_kind"])
    index_name = _decode_scalar(fields[f"{prefix}index_name"])
    if index_kind == "range":
        start = _decode_scalar(fields[f"{prefix}range_start"])
        stop = _decode_scalar(fields[f"{prefix}range_stop"])
        step = _decode_scalar(fields[f"{prefix}range_step"])
        return pd.RangeIndex(start, stop, step, name=index_name)
    labels = decode_element(fields[f"{prefix}index"], namespace).value
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


def _normalize_df_extend_input(other, columns: list) -> tuple[dict, list]:
    """Normalize extend input for a DataFrame into ({col: python_values}, index_labels).

    ``index_labels`` is the list of row labels carried by ``other``. For dict /
    list-of-dict inputs (which have no index), positional labels ``0..n-1`` are
    synthesized (range-compatible). Raises ValueError on column-set mismatch or
    TypeError on a malformed input.
    """
    if isinstance(other, pd.DataFrame):
        other_cols = list(other.columns)
        if set(other_cols) != set(columns):
            raise ValueError(f"extend input columns {other_cols!r} do not match {columns!r}")
        per_col = {c: _python_values(other[c]) for c in columns}
        return per_col, list(other.index)
    if isinstance(other, dict):
        if set(other.keys()) != set(columns):
            raise ValueError(f"extend input columns {list(other.keys())!r} do not match {columns!r}")
        per_col = {}
        for c in columns:
            vals = other[c]
            per_col[c] = _python_values(vals) if isinstance(vals, pd.Series) else list(vals)
        lengths = {len(v) for v in per_col.values()}
        if len(lengths) > 1:
            raise ValueError(
                f"extend dict-of-lists columns have mismatched lengths: { {c: len(per_col[c]) for c in columns} }"
            )
        n = next(iter(lengths)) if lengths else 0
        return per_col, list(range(n))
    if isinstance(other, (list, tuple)):
        rows = list(other)
        bad = [r for r in rows if not isinstance(r, dict)]
        if bad:
            raise TypeError("extend input must be a DataFrame, dict, or list of dict rows")
        per_col = {c: [] for c in columns}
        for r in rows:
            if set(r.keys()) != set(columns):
                raise ValueError(f"extend row columns {list(r.keys())!r} do not match {columns!r}")
            for c in columns:
                per_col[c].append(r[c])
        return per_col, list(range(len(rows)))
    raise TypeError("extend input must be a DataFrame, dict, or list of dict rows")


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
        fields = namespace._redis().hgetall(raw_key)
        if not fields:
            return pd.DataFrame()
        column_names = _decode_scalar(fields["~columns"])
        index_obj = _build_index(fields, namespace, prefix="~")
        data_dict = {}
        for col in column_names:
            col_key = encode_key(col)
            values = decode_element(fields[col_key], namespace).deepcopy()
            dtype_str = _decode_scalar(fields[f"~dt:{col_key}"])
            family = _value_dtype_family(pd.api.types.pandas_dtype(dtype_str))
            cats_ref = fields.get(f"~cat:{col_key}") if family == "category" else None
            ordered = _decode_scalar(fields[f"~ord:{col_key}"]) if family == "category" else None
            data_dict[col] = _reconstruct_series(values, index_obj, dtype_str, col, namespace, cats_ref, ordered)
        return pd.DataFrame(data_dict, columns=list(column_names), index=index_obj)

    @classmethod
    def delete_raw(cls, namespace, raw_key) -> None:
        namespace._redis().delete(raw_key)


class _DfILocIndexer:
    """Positional row indexer for DataFrameObject (read-only)."""

    def __init__(self, owner: "DataFrameObject") -> None:
        self._owner = owner

    def __getitem__(self, key):
        import numpy as np

        owner = self._owner
        if isinstance(key, (int, np.integer)):
            columns = owner._column_names()
            row = {}
            for col in columns:
                col_list = owner._get_column_list(col)
                if col_list is None:
                    raise IndexError("DataFrame has no rows")
                row[col] = col_list[int(key)]
            fields = owner._raw_fields()
            index_obj = _build_index(fields, owner._namespace, prefix="~")
            return pd.Series(row, name=index_obj[int(key)])
        if isinstance(key, slice):
            return owner._positional_row_slice(key)
        raise TypeError(f"iloc expects int or slice, got {type(key).__name__!r}")


class _DfLocIndexer:
    """Label-based row indexer for DataFrameObject (read-only)."""

    def __init__(self, owner: "DataFrameObject") -> None:
        self._owner = owner

    def __getitem__(self, label):
        owner = self._owner
        fields = owner._raw_fields()
        if _decode_scalar(fields["~index_kind"]) == "range":
            raise TypeError(".loc is not supported on a RangeIndex DataFrame; use .iloc")
        index_list = owner._get_index_list()
        if index_list is None:
            raise TypeError(".loc is not supported on a RangeIndex DataFrame; use .iloc")
        labels = index_list.value
        positions = [i for i, lbl in enumerate(labels) if lbl == label]
        if not positions:
            raise KeyError(label)
        columns = owner._column_names()
        full_index = _build_index(fields, owner._namespace, prefix="~")
        sliced_index = pd.Index([labels[p] for p in positions], name=full_index.name)
        data_dict = {}
        for col in columns:
            col_key = encode_key(col)
            col_list = owner._get_column_list(col)
            values = [col_list[p] for p in positions]
            dtype_str = _decode_scalar(fields[f"~dt:{col_key}"])
            family = _value_dtype_family(pd.api.types.pandas_dtype(dtype_str))
            cats_ref = fields.get(f"~cat:{col_key}") if family == "category" else None
            ordered = _decode_scalar(fields[f"~ord:{col_key}"]) if family == "category" else None
            data_dict[col] = _reconstruct_series(
                values, sliced_index, dtype_str, col, owner._namespace, cats_ref, ordered
            )
        result = pd.DataFrame(data_dict, columns=list(columns))
        if len(result) == 1:
            return result.iloc[0]
        return result


class DataFrameObject(GPDCContainerObject):
    """Redis-backed pandas DataFrame object.

    raw_key is a Redis HASH: column value-refs under ``encode_key(col)``, the
    shared index under ``~index``, and scalar metadata under ``~``-prefixed
    fields. See the design spec for the full layout.
    """

    TYPE = "collection:pandas_dataframe"

    def __init__(self, name, namespace, *, canonical_name=None) -> None:
        super().__init__(name, namespace, canonical_name=canonical_name)
        self._index_list_cache = _UNLOADED
        self._column_lists_cache: dict = {}

    @property
    def type(self) -> str:
        return self.TYPE

    @property
    def value(self) -> pd.DataFrame:
        return DataFrameCodec.read_raw(self._namespace, self.raw_key)

    @value.setter
    def value(self, new_value) -> None:
        self._reject_value_replacement()

    # ── internal helpers ─────────────────────────────────────────────

    def _raw_fields(self) -> dict:
        return self._redis().hgetall(self.raw_key)

    def _column_names(self) -> tuple:
        return _decode_scalar(self._redis().hget(self.raw_key, "~columns"))

    def _index_kind(self) -> str:
        return _decode_scalar(self._redis().hget(self.raw_key, "~index_kind"))

    def _get_index_list(self):
        """Return the shared index ListObject wrapper, or None when RangeIndex."""
        if self._index_list_cache is _UNLOADED:
            ref = self._redis().hget(self.raw_key, "~index")
            self._index_list_cache = decode_element(ref, self._namespace) if ref else None
        return self._index_list_cache

    def _get_column_list(self, col):
        """Return a column's value ListObject wrapper (cached)."""
        if col not in self._column_lists_cache:
            ref = self._redis().hget(self.raw_key, encode_key(col))
            self._column_lists_cache[col] = decode_element(ref, self._namespace) if ref else None
        return self._column_lists_cache[col]

    def _invalidate_caches(self) -> None:
        self._index_list_cache = _UNLOADED
        self._column_lists_cache = {}

    def _promote_df_range_to_labels(self, fields: dict, owner_name, policy) -> None:
        """Materialize a RangeIndex into a labels ~index child in-place.

        Note: commits outside the extend pipeline. If the subsequent append
        pipeline fails, the index stays promoted (range→labels) with no rows
        added — semantically valid but representationally changed. Mirrors
        SeriesObject.extend's promote path.
        """
        start = _decode_scalar(fields["~range_start"])
        stop = _decode_scalar(fields["~range_stop"])
        step = _decode_scalar(fields["~range_step"])
        labels = list(pd.RangeIndex(start, stop, step))
        with encode_session(self._namespace):
            ref = encode_element(labels, self._namespace, owner_name, policy)
        r = self._redis()
        mapping = {
            "~index": ref,
            "~index_kind": _encode_extra_value("labels"),
            "~index_dtype": _encode_extra_value("int64"),
        }
        r.hdel(self.raw_key, "~range_start", "~range_stop", "~range_step")
        r.hset(self.raw_key, mapping=mapping)
        self._index_list_cache = _UNLOADED

    def extend(self, other) -> None:
        """Append rows in place. Accepts a DataFrame, a dict of lists, or a list of dicts.

        Mutates the cached object in Redis and returns None.
        """
        from ..lifecycle import policy_from_meta, policy_from_defaults

        r = self._redis()
        fields = self._raw_fields()
        columns = list(_decode_scalar(fields["~columns"]))
        index_kind = _decode_scalar(fields["~index_kind"])

        per_col, appended_labels = _normalize_df_extend_input(other, columns)
        n = len(appended_labels)

        # Range-compatible iff the appended input carries positional labels
        # (a DataFrame with a RangeIndex, or a dict / list-of-dicts which
        # synthesize 0..n-1). Mirrors Series's _is_range_indexed_series.
        if isinstance(other, pd.DataFrame):
            appended_is_range = isinstance(other.index, pd.RangeIndex)
        else:
            appended_is_range = True

        meta = ObjectMeta.load(r, self._canonical_redis_key())
        policy = policy_from_meta(meta) if meta else policy_from_defaults("permanent", 0)
        owner_name = self._data_owner_name()

        # RangeIndex: continue the range, or promote to labels.
        if index_kind == "range":
            range_step = _decode_scalar(fields["~range_step"])
            if range_step == 1 and appended_is_range:
                pass  # range-compatible; bump range_stop below
            else:
                self._promote_df_range_to_labels(fields, owner_name, policy)
                index_kind = "labels"
                fields = self._raw_fields()

        # Per-column validate/cast. Bind all casted python_values to a dict
        # before the encode loop so each stays referenced for the whole
        # encode_session (mirrors the create_raw id-aliasing fix).
        python_values_by_col: dict[str, list] = {}
        for col in columns:
            col_key = encode_key(col)
            dtype_str = _decode_scalar(fields[f"~dt:{col_key}"])
            family = _value_dtype_family(pd.api.types.pandas_dtype(dtype_str))
            raw_values = per_col[col]
            if family == "category":
                existing_cats = decode_element(fields[f"~cat:{col_key}"], self._namespace).value
                for v in raw_values:
                    if not pd.isna(v) and v not in existing_cats:
                        raise ValueError(
                            f"Cannot extend category column {col!r}: value {v!r} is not in "
                            f"existing categories {existing_cats!r}."
                        )
                # Normalize NA sentinels (pd.NA / np.nan / None) to None so
                # encode_element can store them; the non-category branch gets
                # this for free via _python_values, but category skips that cast.
                python_values_by_col[col] = [None if pd.isna(v) else v for v in raw_values]
            else:
                try:
                    casted = pd.Series(raw_values, dtype=dtype_str)
                except (ValueError, TypeError) as exc:
                    raise ValueError(f"Cannot extend column {col!r} with dtype {dtype_str!r}: {exc}") from exc
                python_values_by_col[col] = _python_values(casted)

        # One MULTI/EXEC pipeline: RPUSH each column + shared index lockstep.
        pipe = r.pipeline(transaction=True)
        with encode_session(self._namespace):
            encoded_per_col: dict[str, list[str]] = {}
            for col in columns:
                encoded_per_col[col] = [
                    encode_element(v, self._namespace, owner_name, policy) for v in python_values_by_col[col]
                ]
            if index_kind == "labels":
                encoded_idx = [encode_element(lbl, self._namespace, owner_name, policy) for lbl in appended_labels]
            else:
                encoded_idx = None
        for col in columns:
            enc = encoded_per_col[col]
            if enc:
                pipe.rpush(self._get_column_list(col).raw_key, *enc)
        if index_kind == "labels":
            if encoded_idx:
                pipe.rpush(self._get_index_list().raw_key, *encoded_idx)
        else:
            new_stop = _decode_scalar(fields["~range_stop"]) + n
            pipe.hset(self.raw_key, "~range_stop", _encode_extra_value(new_stop))
        pipe.execute()

        self._invalidate_caches()
        self._touch()

    def deepcopy(self) -> pd.DataFrame:
        return self.value.copy(deep=True)

    def _current_item_count(self) -> int:
        return len(self)

    # ── public read API ──────────────────────────────────────────────

    def __len__(self) -> int:
        if self._index_kind() == "range":
            fields = self._raw_fields()
            return _decode_scalar(fields["~range_stop"]) - _decode_scalar(fields["~range_start"])
        idx = self._get_index_list()
        return len(idx) if idx is not None else 0

    @property
    def columns(self) -> tuple:
        return self._column_names()

    @property
    def index_name(self):
        return _decode_scalar(self._redis().hget(self.raw_key, "~index_name"))

    def __contains__(self, col) -> bool:
        try:
            encoded = encode_key(col)
        except TypeError:
            return False
        return bool(self._redis().hexists(self.raw_key, encoded))

    def __iter__(self):
        return iter(self._column_names())

    def __getitem__(self, col) -> pd.Series:
        columns = self._column_names()
        if col not in columns:
            raise KeyError(col)
        col_key = encode_key(col)
        data_list = self._get_column_list(col)
        values = data_list.deepcopy() if data_list is not None else []
        fields = self._raw_fields()
        dtype_str = _decode_scalar(fields[f"~dt:{col_key}"])
        family = _value_dtype_family(pd.api.types.pandas_dtype(dtype_str))
        cats_ref = fields.get(f"~cat:{col_key}") if family == "category" else None
        ordered = _decode_scalar(fields[f"~ord:{col_key}"]) if family == "category" else None
        index_obj = _build_index(fields, self._namespace, prefix="~")
        return _reconstruct_series(values, index_obj, dtype_str, col, self._namespace, cats_ref, ordered)

    def __setitem__(self, col, values) -> None:
        """Add or replace a column. ``len(values)`` must equal the current row count."""
        from ..lifecycle import policy_from_meta, policy_from_defaults

        r = self._redis()
        fields = self._raw_fields()
        columns = list(_decode_scalar(fields["~columns"]))
        owner_name = self._data_owner_name()
        meta = ObjectMeta.load(r, self._canonical_redis_key())
        policy = policy_from_meta(meta) if meta else policy_from_defaults("permanent", 0)

        expected = self.__len__()
        if expected == 0 and len(values) > 0:
            raise ValueError(
                "cannot introduce rows by adding a column to an empty DataFrame; create the DataFrame with initial data"
            )
        if len(values) != expected:
            raise ValueError(f"column length {len(values)} does not match row count {expected}")

        inferred = pd.Series(values)
        family = _value_dtype_family(inferred.dtype)
        if family is None:
            raise UnsupportedDataFrameError(
                f"Column {col!r} has dtype {inferred.dtype!r} which is not supported by DataFrameObject."
            )
        col_key = encode_key(col)
        replacing = col in columns
        old_family = None
        if replacing:
            old_ref = r.hget(self.raw_key, col_key)
            self._release_element_ref(old_ref)
            old_dtype_str = _decode_scalar(fields[f"~dt:{col_key}"])
            old_family = _value_dtype_family(pd.api.types.pandas_dtype(old_dtype_str))
            if old_family == "category":
                self._release_element_ref(r.hget(self.raw_key, f"~cat:{col_key}"))

        data_values = _python_values(inferred)
        with encode_session(self._namespace):
            mapping = {
                col_key: encode_element(data_values, self._namespace, owner_name, policy),
                f"~dt:{col_key}": _encode_extra_value(str(inferred.dtype)),
            }
            if family == "category":
                mapping[f"~cat:{col_key}"] = encode_element(
                    inferred.cat.categories.tolist(), self._namespace, owner_name, policy
                )
                mapping[f"~ord:{col_key}"] = _encode_extra_value(bool(inferred.cat.ordered))

        if replacing and old_family == "category" and family != "category":
            r.hdel(self.raw_key, f"~cat:{col_key}", f"~ord:{col_key}")
        r.hset(self.raw_key, mapping=mapping)

        if not replacing:
            columns.append(col)
            r.hset(self.raw_key, "~columns", _encode_extra_value(tuple(columns)))

        self._invalidate_caches()
        self._touch()

    def __delitem__(self, col) -> None:
        """Drop a column. The shared index is retained (even for the last column)."""
        r = self._redis()
        fields = self._raw_fields()
        columns = list(_decode_scalar(fields["~columns"]))
        if col not in columns:
            raise KeyError(col)
        col_key = encode_key(col)
        old_ref = r.hget(self.raw_key, col_key)
        dtype_str = _decode_scalar(fields[f"~dt:{col_key}"])
        family = _value_dtype_family(pd.api.types.pandas_dtype(dtype_str))
        old_cat_ref = r.hget(self.raw_key, f"~cat:{col_key}") if family == "category" else None

        fields_to_del = [col_key, f"~dt:{col_key}"]
        if family == "category":
            fields_to_del.extend([f"~cat:{col_key}", f"~ord:{col_key}"])
        r.hdel(self.raw_key, *fields_to_del)
        self._release_element_ref(old_ref)
        if old_cat_ref:
            self._release_element_ref(old_cat_ref)

        columns.remove(col)
        r.hset(self.raw_key, "~columns", _encode_extra_value(tuple(columns)))

        self._invalidate_caches()
        self._touch()

    @property
    def iloc(self) -> "_DfILocIndexer":
        return _DfILocIndexer(self)

    @property
    def loc(self) -> "_DfLocIndexer":
        return _DfLocIndexer(self)

    def _positional_row_slice(self, sl: slice) -> pd.DataFrame:
        columns = self._column_names()
        fields = self._raw_fields()
        index_obj = _build_index(fields, self._namespace, prefix="~")
        sliced_index = index_obj[sl]
        data_dict = {}
        for col in columns:
            col_key = encode_key(col)
            col_list = self._get_column_list(col)
            values = col_list[sl] if col_list is not None else []
            dtype_str = _decode_scalar(fields[f"~dt:{col_key}"])
            family = _value_dtype_family(pd.api.types.pandas_dtype(dtype_str))
            cats_ref = fields.get(f"~cat:{col_key}") if family == "category" else None
            ordered = _decode_scalar(fields[f"~ord:{col_key}"]) if family == "category" else None
            data_dict[col] = _reconstruct_series(
                values, sliced_index, dtype_str, col, self._namespace, cats_ref, ordered
            )
        return pd.DataFrame(data_dict, columns=list(columns))


def register() -> None:
    """Register the pandas Series + DataFrame types with GPDCTypeRegistry (idempotent)."""
    GPDCTypeRegistry.register(
        python_type=pd.Series,
        gpdc_type="collection:pandas_series",
        object_class=SeriesObject,
        codec_class=SeriesCodec,
        priority=0,
    )
    GPDCTypeRegistry.register(
        python_type=pd.DataFrame,
        gpdc_type="collection:pandas_dataframe",
        object_class=DataFrameObject,
        codec_class=DataFrameCodec,
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
