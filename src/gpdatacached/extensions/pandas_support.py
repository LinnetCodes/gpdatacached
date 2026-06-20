"""Optional pandas ``Series`` / ``DataFrame`` support for gpdatacached.

pandas is an optional dependency. Register it explicitly to make
:class:`pd.Series` and :class:`pd.DataFrame` cacheable GPDC types with live
:class:`SeriesObject` / :class:`DataFrameObject` wrappers::

    from gpdatacached.extensions.pandas_support import register
    register()

Scope at a glance
-----------------
* **Row indexes**: ``RangeIndex`` (default int index, stored compactly) and any
  labelled index including ``pd.MultiIndex`` (stored one ``collection:list``
  child per level). ``.index_names`` always returns a tuple.
* **DataFrame columns**: single-level (scalar names) or ``pd.MultiIndex``
  (tuple names); the row and column axes are independent and compose (a
  DataFrame may have both a row and a column MultiIndex).
* **Value dtypes**: int/float (incl. nullable ``Int64``/``Float64``), bool,
  str/string, object, datetime (naive & tz-aware), category.
* **Mutation**: append-focused only — row ``extend`` (Series & DataFrame) and,
  on DataFrame, column ``__setitem__`` / ``__delitem__`` (structural
  add/replace/drop). No cell-level or in-place row mutation.

Usage guidance — read selectively, then work locally
-----------------------------------------------------
gpdatacached is a **cache shared across processes**, not an in-process pandas
workspace. The intended pattern is: *one writer caches a large structure; many
reader processes each pull the subset they need for the current task, then
operate on that local copy.*

* **Fetch your subset in one selective pass, then work locally.** Use the
  online accessors below to read just the rows/columns you need; do all
  further processing (filtering, joining, aggregating) on the returned plain
  ``pandas`` object. Do not ping the cache per-row or per-cell.
* **Online reads are supported only when meaningfully cheaper than full
  restore.** Anything that approaches the cost of ``.value`` (e.g.
  ``.loc[slice]``, ``.loc[list]``, ``.iloc[list]``) is rejected with a
  ``TypeError`` pointing at ``.value``.

Per-accessor cost (the decision guide)::

    .iloc[int]          O(1)              a single positional value/cell
    .iloc[a:b]          O(b-a)            a positional slice
    .loc[key]           O(N), selective   depth-k prefix label scan; decodes
                                           only matched rows but reads every
                                           index level — do NOT put in a hot loop
    df[col] / df[tuple] O(N), one column  (DataFrame) a single column
    df[level0] / df[list] O(N_cols) + O(matches·N)
                                         column-axis selection (cheap; few cols)
    .value              full restore      O(N) — the escape hatch

Decision rule:

* **One-shot selective read** (rows/columns wanted ≪ total) → use the online
  accessor (``.iloc`` / ``.loc`` / ``__getitem__``).
* **Repeated or heavy work on the same data** — sorting, many label lookups,
  complex pandas semantics, or any operation the online API rejects → call
  ``.value`` once to materialize the local ``pandas`` object and work on it.

Notes
-----
* ``.loc`` matches labels with Python ``==``; ``nan`` labels therefore do not
  match (``nan != nan``).
* Returned partial results retain the full stored index (no level is dropped).
"""

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
    "UnsupportedElementTypeError",
    "register",
]


_ELEMENT_SCAN_FAMILIES = {"object_or_string", "category"}


class UnsupportedElementTypeError(TypeError):
    """A Series/DataFrame element is not encodable by a GPDCScalarCodec subclass.

    Raised when an element is a container type (list/dict/set/container-registered
    pydantic model) or an unsupported type. Series/DataFrame elements must be
    scalar types encodable by a GPDCScalarCodec subclass.
    """

    _GUIDANCE = (
        "\nSeries/DataFrame elements must be scalar types encodable by a "
        "GPDCScalarCodec subclass (int/float/bool/str/datetime/date/time/tuple/None, or a "
        "scalar-registered custom type). Container types are not allowed as elements."
    )

    def __init__(self, message: str) -> None:
        super().__init__(message + self._GUIDANCE)


def _to_python_native(value):
    """Normalize a numpy scalar to its Python native equivalent.

    GPDCTypeRegistry.lookup_by_python_type is inconsistent on raw numpy scalars
    (it rejects np.int64 / np.bool_ as unsupported). Mirrors the .tolist()
    conversion the encode path already relies on. `isinstance(v, np.generic)`
    is True for every numpy scalar dtype and `.item()` maps each to its native.
    """
    if isinstance(value, np.generic):
        return value.item()
    return value


def _is_na_sentinel(value) -> bool:
    """True for missing-value sentinels that are always allowed as elements.

    None / pandas NA / pandas NaT are valid NA markers, consistent with
    _python_values/_python_labels which normalize these to None on the encode
    path. Uses identity checks rather than pd.isna: pd.isna on a list/dict
    returns a bool-ambiguous array, and those containers must fall through to
    the codec lookup below so they get rejected.
    """
    return value is None or value is pd.NA or value is pd.NaT


def _assert_scalar_encodable(values, context: str) -> None:
    """Raise UnsupportedElementTypeError if any value is not encodable by a
    GPDCScalarCodec subclass.

    `context` is a human-readable description for the error message (e.g.
    "Series element", "DataFrame column 'o' element", "index label",
    "category value"). NA sentinels (None / pandas NA / NaT) are always allowed.
    """
    from ..codec import GPDCScalarCodec
    from ..types import GPDCTypeRegistry

    for raw_value in values:
        if _is_na_sentinel(raw_value):
            continue
        value = _to_python_native(raw_value)
        if _is_na_sentinel(value):
            continue
        try:
            entry = GPDCTypeRegistry.lookup_by_python_type(value)
        except TypeError:
            raise UnsupportedElementTypeError(
                f"{context} of type {type(raw_value).__name__!r} is not a supported scalar type."
            )
        if not issubclass(entry.codec_class, GPDCScalarCodec):
            raise UnsupportedElementTypeError(
                f"{context} of type {type(raw_value).__name__!r} is a container type; only scalar elements are allowed."
            )


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
    """Classify a pandas index: 'range', 'levels', or None if unsupported.

    'levels' covers every labelled index incl. MultiIndex (L>=1).
    """
    if isinstance(index, pd.RangeIndex):
        return "range"
    if isinstance(index, pd.MultiIndex):
        for i in range(index.nlevels):
            if _value_dtype_family(index.get_level_values(i).dtype) is None:
                return None
        return "levels"
    return "levels" if _value_dtype_family(index.dtype) is not None else None


def _validate_dataframe(value: pd.DataFrame) -> tuple[list, str]:
    """Validate a DataFrame for storage.

    Returns ``(column_names, index_kind)``. Raises ``UnsupportedDataFrameError``
    on duplicate column names, an unsupported per-column dtype, or an
    unsupported row-index dtype. ``index_kind`` is ``_index_kind``'s result
    (``range`` or ``levels``; single-level labelled and MultiIndex both yield
    ``levels``).
    """
    names = list(value.columns)
    seen: set = set()
    for n in names:
        if n in seen:
            raise UnsupportedDataFrameError(f"Duplicate column names are not supported by DataFrameObject: {names!r}.")
        seen.add(n)
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
    """Materialize Series values as Python natives with NA -> None.

    Numpy scalars (e.g. np.int64/np.bool_ inside an object-dtype Series, which
    .tolist() leaves untouched) are normalized to Python natives so the element
    codec lookup succeeds. Container values (list/dict/set) are not numpy
    scalars and pass through unchanged.
    """
    is_na = pd.isna(series)
    return [None if na else _to_python_native(x) for x, na in zip(series.tolist(), list(is_na))]


def _python_labels(index) -> list:
    """Materialize Index labels as Python natives with NA -> None.

    Numpy scalars are normalized to Python natives (see _python_values).
    """
    is_na = pd.isna(index)
    return [None if na else _to_python_native(x) for x, na in zip(index.tolist(), list(is_na))]


def _level_labels_and_dtype(index, level_i: int) -> tuple[list, str]:
    """Return (python-native labels, dtype-string) for one MultiIndex level."""
    level_vals = index.get_level_values(level_i)
    return _python_labels(level_vals), str(level_vals.dtype)


def _infer_promote_dtype(labels: list) -> str:
    """Honest combined dtype for a range→levels promote: int64 if all appended
    labels cast to int64, else object. (RangeIndex values are always int, so the
    combined level dtype is determined by whether the appended labels are int64.)"""
    try:
        pd.array(labels, dtype="int64")
        return "int64"
    except (TypeError, ValueError):
        return "object"


def _build_levels_index(fields: dict, namespace, prefix: str = "") -> "pd.Index | pd.MultiIndex":
    """Rebuild a MultiIndex (L>=1) from per-level ref fields."""
    names = _decode_scalar(fields[f"{prefix}index_names"])
    dtypes = _decode_scalar(fields[f"{prefix}index_dtypes"])
    L = len(names)
    arrays = []
    for i in range(L):
        labels = decode_element(fields[f"{prefix}index_l{i}"], namespace).value
        try:
            arrays.append(pd.array(labels, dtype=dtypes[i]))
        except (TypeError, ValueError):
            # Create-time validation already enforced dtype<->labels consistency,
            # so reaching here means corruption or a future new dtype; on a cache
            # read path it is safer to infer than to raise.
            arrays.append(pd.array(labels))
    if L == 1:
        return pd.Index(arrays[0], name=names[0])
    return pd.MultiIndex.from_arrays(arrays, names=names)


def _build_columns_index(fields: dict, column_names) -> "pd.Index | pd.MultiIndex | list":
    """Build the DataFrame column index. MultiIndex (with level names) when
    ``~columns_names`` is present, else the plain column-name list."""
    if "~columns_names" in fields:
        col_names = _decode_scalar(fields["~columns_names"])
        return pd.MultiIndex.from_tuples(list(column_names), names=col_names)
    return list(column_names)


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


def _reconstruct_column(values, index_obj, fields, col, namespace) -> pd.Series:
    """Reconstruct one DataFrame column from decoded values + raw-hash metadata.

    Shared by DataFrameCodec.read_raw, DataFrameObject.__getitem__,
    _positional_row_slice, and _DfLocIndexer. Reads the per-column
    ``~dt:``/``~cat:``/``~ord:`` metadata from ``fields`` and delegates to
    _reconstruct_series. ``values`` is the already-decoded list of cell values
    (caller-supplied, since each read path fetches values differently).
    """
    col_key = encode_key(col)
    dtype_str = _decode_scalar(fields[f"~dt:{col_key}"])
    family = _value_dtype_family(pd.api.types.pandas_dtype(dtype_str))
    cats_ref = fields.get(f"~cat:{col_key}") if family == "category" else None
    ordered = _decode_scalar(fields[f"~ord:{col_key}"]) if family == "category" else None
    return _reconstruct_series(values, index_obj, dtype_str, col, namespace, cats_ref, ordered)


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
    """Build a RangeIndex from raw-hash range fields.

    Handles the ``range`` index kind only. ``levels`` indexes (single-level
    labelled or MultiIndex) are rebuilt via ``_build_levels_index`` directly
    at their call sites (``DataFrameCodec.read_raw``, ``SeriesCodec.read_raw``,
    ``DataFrameObject._build_full_row_index``). ``prefix`` is "" for Series
    raw-hash field names (``index_kind``) and "~" for DataFrame raw-hash field
    names (``~index_kind``).
    """
    index_names = _decode_scalar(fields[f"{prefix}index_names"])
    name = index_names[0] if index_names else None
    start = _decode_scalar(fields[f"{prefix}range_start"])
    stop = _decode_scalar(fields[f"{prefix}range_stop"])
    step = _decode_scalar(fields[f"{prefix}range_step"])
    return pd.RangeIndex(start, stop, step, name=name)


def _normalize_extend_input(other) -> tuple[list, list, int]:
    """Return (labels, data_values, nlevels) from extend input.

    ``labels`` is a list of tuple labels (one per appended row); each tuple's
    length is the appended index's level count. ``nlevels`` is that level count.
    Accepts a pd.Series, a list of pd.Series, or an iterable of (index, value)
    tuples. The level-count check is the caller's responsibility.
    """
    if isinstance(other, pd.Series):
        idx = other.index
        nlevels = getattr(idx, "nlevels", 1)
        if isinstance(idx, pd.MultiIndex):
            labels = [tuple(r) for r in idx]
        else:
            labels = [(r,) for r in idx]
        return labels, _python_values(other), nlevels
    if isinstance(other, (list, tuple)) and len(other) > 0 and isinstance(other[0], pd.Series):
        idx0 = other[0].index
        nlevels = getattr(idx0, "nlevels", 1)
        labels: list = []
        vals: list = []
        for s in other:
            si = s.index
            if isinstance(si, pd.MultiIndex):
                labels.extend(tuple(r) for r in si)
            else:
                labels.extend((r,) for r in si)
            vals.extend(_python_values(s))
        return labels, vals, nlevels
    pairs = list(other)
    bad = [p for p in pairs if not (isinstance(p, tuple) and len(p) == 2)]
    if bad:
        raise TypeError("extend input must be a Series, a list of Series, or (index, value) tuples")
    labels = []
    vals = []
    nlevels = None
    for lbl, v in pairs:
        if not isinstance(lbl, tuple):
            lbl = (lbl,)
        if nlevels is None:
            nlevels = len(lbl)
        elif len(lbl) != nlevels:
            raise ValueError("inconsistent index label depths in extend input")
        labels.append(lbl)
        vals.append(v)
    if nlevels is None:
        nlevels = 1
    return labels, vals, nlevels


def _is_range_indexed_series(other) -> bool:
    """True when `other` is a Series with a RangeIndex (so a range Series can stay a range)."""
    return isinstance(other, pd.Series) and isinstance(other.index, pd.RangeIndex)


def _normalize_df_extend_input(other, columns: list, expect_levels: int | None = None) -> tuple[dict, list[list], int]:
    """Normalize extend input for a DataFrame.

    Returns ``(per_col, index_labels_per_level, nlevels)`` where ``per_col`` maps
    each column to its python-native values, ``index_labels_per_level`` is a list
    of ``nlevels`` lists (one per index level, each the appended labels for that
    level), and ``nlevels`` is the appended index's level count.

    For dict / list-of-dict inputs (which carry no index), positional labels
    ``0..n-1`` are synthesized (range-compatible). A genuine MultiIndex-row
    DataFrame (``expect_levels >= 2``) rejects dict/list inputs with a TypeError
    pointing at ``.value`` — those forms synthesise positional labels a
    MultiIndex cannot supply. Raises ValueError on column-set mismatch.
    """
    if isinstance(other, pd.DataFrame):
        other_cols = list(other.columns)
        if set(other_cols) != set(columns):
            raise ValueError(f"extend input columns {other_cols!r} do not match {columns!r}")
        per_col = {c: _python_values(other[c]) for c in columns}
        idx = other.index
        nlevels = getattr(idx, "nlevels", 1)
        if isinstance(idx, pd.MultiIndex):
            index_labels_per_level = [_python_labels(idx.get_level_values(i)) for i in range(nlevels)]
        else:
            index_labels_per_level = [_python_labels(idx)]
            nlevels = 1
        return per_col, index_labels_per_level, nlevels
    multi_rejected = expect_levels is not None and expect_levels > 1
    if isinstance(other, dict):
        if multi_rejected:
            raise TypeError(
                "extend a MultiIndex-row DataFrame with a DataFrame only; "
                "for other inputs call .value for the full pandas object and operate on it locally."
            )
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
        return per_col, [list(range(n))], 1
    if isinstance(other, (list, tuple)):
        if multi_rejected:
            raise TypeError(
                "extend a MultiIndex-row DataFrame with a DataFrame only; "
                "for other inputs call .value for the full pandas object and operate on it locally."
            )
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
        return per_col, [list(range(len(rows)))], 1
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

        # Validate elements are scalar-encodable BEFORE encoding (no Redis write yet).
        for col in column_names:
            if _value_dtype_family(value[col].dtype) in _ELEMENT_SCAN_FAMILIES:
                _assert_scalar_encodable(col_values[col], f"DataFrame column {col!r} element")
            if col in col_cats:
                _assert_scalar_encodable(col_cats[col], f"DataFrame column {col!r} category value")

        fields: dict[str, str] = {}
        with encode_session(namespace):
            for col in column_names:
                col_key = encode_key(col)
                fields[col_key] = encode_element(col_values[col], namespace, owner_name, policy)
                if col in col_cats:
                    fields[f"~cat:{col_key}"] = encode_element(col_cats[col], namespace, owner_name, policy)
            if kind == "levels":
                # Bind all level label lists to locals before encoding (id-aliasing guard).
                level_labels = [_level_labels_and_dtype(index, i) for i in range(index.nlevels)]
                for i, (labels, _dt) in enumerate(level_labels):
                    _assert_scalar_encodable(labels, f"DataFrame index level {i} label")
                    fields[f"~index_l{i}"] = encode_element(labels, namespace, owner_name, policy)

        for col in column_names:
            col_key = encode_key(col)
            col_series = value[col]
            fields[f"~dt:{col_key}"] = _encode_extra_value(str(col_series.dtype))
            if _value_dtype_family(col_series.dtype) == "category":
                fields[f"~ord:{col_key}"] = _encode_extra_value(bool(col_series.cat.ordered))

        fields["~columns"] = _encode_extra_value(tuple(column_names))
        # Column level-names stored only for MultiIndex columns; a single-level
        # column index .name is out of scope (see Plan 2 Task 1 / spec §3.2).
        if isinstance(value.columns, pd.MultiIndex):
            fields["~columns_names"] = _encode_extra_value(tuple(value.columns.names))
        fields["~index_kind"] = _encode_extra_value(kind)
        fields["~index_names"] = _encode_extra_value(tuple(index.names))
        if kind == "levels":
            fields["~index_dtypes"] = _encode_extra_value(tuple(dt for _, dt in level_labels))
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
        index_kind = _decode_scalar(fields["~index_kind"])
        if index_kind == "range":
            index_obj = _build_index(fields, namespace, prefix="~")
        else:  # levels
            index_obj = _build_levels_index(fields, namespace, prefix="~")
        data_dict = {}
        for col in column_names:
            col_key = encode_key(col)
            values = decode_element(fields[col_key], namespace).deepcopy()
            data_dict[col] = _reconstruct_column(values, index_obj, fields, col, namespace)
        columns = _build_columns_index(fields, column_names)
        return pd.DataFrame(data_dict, columns=columns, index=index_obj)

    @classmethod
    def delete_raw(cls, namespace, raw_key) -> None:
        namespace._redis().delete(raw_key)


class _DfILocIndexer:
    """Positional row indexer for DataFrameObject (read-only)."""

    def __init__(self, owner: "DataFrameObject") -> None:
        self._owner = owner

    def __getitem__(self, key):
        owner = self._owner
        if isinstance(key, (int, np.integer)):
            fields = owner._raw_fields()
            columns = owner._column_names()
            values = []
            for col in columns:
                col_list = owner._get_column_list(col)
                if col_list is None:
                    raise IndexError("DataFrame has no rows")
                values.append(col_list[int(key)])
            index_obj = owner._build_full_row_index()[int(key)]
            return pd.Series(values, index=_build_columns_index(fields, columns), name=index_obj)
        if isinstance(key, slice):
            return owner._positional_row_slice(key)
        raise TypeError(
            ".iloc list (positional fancy) indexing is not supported on this cache object; "
            "use .iloc with an int or slice, or call .value for the full pandas object."
        )


class _DfLocIndexer:
    """Label-based row indexer for DataFrameObject (read-only).

    Supports scalar / tuple depth-k prefix selection on a MultiIndex (or
    single-level labelled) row index. Rejects slice and list with a TypeError
    pointing to ``.value`` (the cost-bar from the design spec). Full-depth
    (``k == L``) with exactly one matching row returns that row as a
    ``pd.Series``; partial depth or multiple matches return a ``pd.DataFrame``.
    """

    def __init__(self, owner: "DataFrameObject") -> None:
        self._owner = owner

    def __getitem__(self, key):
        owner = self._owner
        if isinstance(key, slice):
            raise TypeError(
                ".loc slices are not supported on this cache object; "
                "call .value for the full pandas object and operate on it locally, "
                "or use .iloc for positional slicing."
            )
        if isinstance(key, list):
            raise TypeError(
                ".loc label-list selection is not supported on this cache object; "
                "call .value for the full pandas object and operate on it locally."
            )
        fields = owner._raw_fields()
        if _decode_scalar(fields["~index_kind"]) == "range":
            raise TypeError(
                ".loc on a RangeIndex is not supported on this cache object; "
                "use .iloc for positional slicing, or call .value for the full pandas object."
            )
        names = _decode_scalar(fields["~index_names"])
        L = len(names)
        key_tuple = key if isinstance(key, tuple) else (key,)
        k = len(key_tuple)
        if k > L:
            raise KeyError(f"key depth {k} exceeds index levels {L}")
        # Fetch all L level values once (ListObject.value is uncached -> each call is a Redis lrange).
        all_level_values = [owner._get_level_list(i).value for i in range(L)]
        # NA-label matching follows Python == semantics; nan labels won't match (pre-existing behaviour).
        positions = [
            pos for pos in range(len(owner)) if all(all_level_values[i][pos] == key_tuple[i] for i in range(k))
        ]
        if not positions:
            raise KeyError(key)
        columns = owner._column_names()
        # Build the result row index from the SAME all_level_values (matched positions, all L levels).
        arrays = [[lvl[p] for p in positions] for lvl in all_level_values]
        if L == 1:
            sliced_index = pd.Index(arrays[0], name=names[0])
        else:
            sliced_index = pd.MultiIndex.from_arrays(arrays, names=names)
        data_dict = {}
        for col in columns:
            col_list = owner._get_column_list(col)
            values = [col_list[p] for p in positions]
            data_dict[col] = _reconstruct_column(values, sliced_index, fields, col, owner._namespace)
        result = pd.DataFrame(data_dict, columns=_build_columns_index(fields, columns))
        if k == L and len(positions) == 1:
            return result.iloc[0]  # single full-depth match -> row as pd.Series
        return result


class DataFrameObject(GPDCContainerObject):
    """Redis-backed pandas DataFrame object.

    raw_key is a Redis HASH: column value-refs under ``encode_key(col)``, the
    per-level index labels under ``~index_l{i}`` (with ``~index_kind`` /
    ``~index_names`` / ``~index_dtypes`` scalar metadata), and other scalar
    metadata under ``~``-prefixed fields. See the design spec for the full
    layout.
    """

    TYPE = "collection:pandas_dataframe"

    def __init__(self, name, namespace, *, canonical_name=None) -> None:
        super().__init__(name, namespace, canonical_name=canonical_name)
        self._column_lists_cache: dict = {}
        # Per-level index ListObject cache (L>=1; the extend pipeline and the
        # depth-k read paths both go through _get_level_list). Cleared on
        # mutation so a stale ref is never read.
        self._level_lists_cache: dict = {}

    @property
    def type(self) -> str:
        return self.TYPE

    @property
    def value(self) -> pd.DataFrame:
        """Full materialization as a plain :class:`pandas.DataFrame`.

        Use this for repeated or heavy work on the same data (sorting, many
        lookups, complex pandas semantics, or operations the online API
        rejects). For a one-shot selective read, prefer :attr:`iloc` / :attr:`loc`
        / ``df[col]``. See the module docstring for the full usage guidance.
        """
        return DataFrameCodec.read_raw(self._namespace, self.raw_key)

    @value.setter
    def value(self, new_value) -> None:
        self._reject_value_replacement()

    # ── internal helpers ─────────────────────────────────────────────

    def _raw_fields(self) -> dict:
        return self._redis().hgetall(self.raw_key)

    def _column_names(self) -> tuple:
        return _decode_scalar(self._redis().hget(self.raw_key, "~columns"))

    def _columns_names_or_none(self):
        raw = self._redis().hget(self.raw_key, "~columns_names")
        return _decode_scalar(raw) if raw else None

    def _select_one_series(self, col, fields) -> pd.Series:
        col_list_obj = self._get_column_list(col)
        values = col_list_obj.deepcopy() if col_list_obj is not None else []
        index_obj = self._build_full_row_index()
        return _reconstruct_column(values, index_obj, fields, col, self._namespace)

    def _select_columns(self, col_list) -> pd.DataFrame:
        fields = self._raw_fields()
        index_obj = self._build_full_row_index()
        data = {}
        for c in col_list:
            col_list_obj = self._get_column_list(c)
            values = col_list_obj.deepcopy() if col_list_obj is not None else []
            data[c] = _reconstruct_column(values, index_obj, fields, c, self._namespace)
        columns = _build_columns_index(fields, col_list)
        return pd.DataFrame(data, columns=columns, index=index_obj)

    def _index_kind(self) -> str:
        return _decode_scalar(self._redis().hget(self.raw_key, "~index_kind"))

    def _get_level_list(self, level_i: int):
        """Return the per-level index ListObject wrapper, or None when the level is absent."""
        if level_i not in self._level_lists_cache:
            ref = self._redis().hget(self.raw_key, f"~index_l{level_i}")
            self._level_lists_cache[level_i] = decode_element(ref, self._namespace) if ref else None
        return self._level_lists_cache[level_i]

    def _build_full_row_index(self):
        """Rebuild the full row pd.Index / pd.MultiIndex from raw-hash fields."""
        fields = self._raw_fields()
        kind = _decode_scalar(fields["~index_kind"])
        if kind == "range":
            return _build_index(fields, self._namespace, prefix="~")
        return _build_levels_index(fields, self._namespace, prefix="~")

    def _get_column_list(self, col):
        """Return a column's value ListObject wrapper (cached)."""
        if col not in self._column_lists_cache:
            ref = self._redis().hget(self.raw_key, encode_key(col))
            self._column_lists_cache[col] = decode_element(ref, self._namespace) if ref else None
        return self._column_lists_cache[col]

    def _invalidate_caches(self) -> None:
        self._column_lists_cache = {}
        self._level_lists_cache = {}

    def _promote_df_range_to_labels(self, fields: dict, owner_name, policy, level_dtype="int64") -> None:
        """Materialize a RangeIndex into an L=1 ``levels`` index in place.

        Emits ``~index_l0`` / ``~index_kind=levels`` / ``~index_dtypes`` (matching
        the new levels storage layout; ``read_raw`` no longer reads ``labels``).
        ``level_dtype`` lets the extend caller pass an honest combined dtype so
        subsequent extends cast against a truthful dtype instead of a stale
        int64 (mirrors the Series Task-5 fix). Defaults to ``int64`` so today's
        single-level extend caller behaves as before.

        Note: commits outside the extend pipeline. If the subsequent append
        pipeline fails, the index stays promoted (range→levels) with no rows
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
            "~index_l0": ref,
            "~index_kind": _encode_extra_value("levels"),
            "~index_dtypes": _encode_extra_value((level_dtype,)),
        }
        r.hdel(self.raw_key, "~range_start", "~range_stop", "~range_step")
        r.hset(self.raw_key, mapping=mapping)
        self._level_lists_cache = {}

    def extend(self, other) -> None:
        """Append rows in place. Accepts a DataFrame, a dict of lists, or a list of dicts.

        Mutates the cached object in Redis and returns None. For a genuine
        MultiIndex-row DataFrame (L>=2), only a DataFrame with a matching
        MultiIndex is accepted (dict/list inputs synthesise positional labels a
        MultiIndex cannot supply -> TypeError; use ``.value``).
        """
        from ..lifecycle import policy_from_meta, policy_from_defaults

        r = self._redis()
        fields = self._raw_fields()
        columns = list(_decode_scalar(fields["~columns"]))
        index_kind = _decode_scalar(fields["~index_kind"])
        names = _decode_scalar(fields["~index_names"])

        expect_levels = len(names) if index_kind == "levels" else None
        per_col, index_labels_per_level, got_levels = _normalize_df_extend_input(
            other, columns, expect_levels=expect_levels
        )
        n = len(index_labels_per_level[0]) if index_labels_per_level else 0

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

        # Per-column validate/cast BEFORE any Redis write (promote/encode) so a
        # rejected input leaves the cached object untouched. Bind all casted
        # python_values to a dict before the encode loop so each stays referenced
        # for the whole encode_session (mirrors the create_raw id-aliasing fix).
        python_values_by_col: dict[str, list] = {}
        for col in columns:
            col_key = encode_key(col)
            dtype_str = _decode_scalar(fields[f"~dt:{col_key}"])
            family = _value_dtype_family(pd.api.types.pandas_dtype(dtype_str))
            raw_values = per_col[col]
            if family in _ELEMENT_SCAN_FAMILIES:
                _assert_scalar_encodable(raw_values, f"DataFrame column {col!r} element")
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

        # RangeIndex: continue the range, or promote to L=1 levels.
        if index_kind == "range":
            range_step = _decode_scalar(fields["~range_step"])
            if range_step == 1 and appended_is_range:
                # range-compatible: just bump range_stop + append columns.
                pipe = r.pipeline(transaction=True)
                with encode_session(self._namespace):
                    encoded_per_col = {
                        col: [encode_element(v, self._namespace, owner_name, policy) for v in python_values_by_col[col]]
                        for col in columns
                    }
                for col in columns:
                    enc = encoded_per_col[col]
                    if enc:
                        pipe.rpush(self._get_column_list(col).raw_key, *enc)
                new_stop = _decode_scalar(fields["~range_stop"]) + n
                pipe.hset(self.raw_key, "~range_stop", _encode_extra_value(new_stop))
                pipe.execute()
                self._invalidate_caches()
                self._touch()
                return
            # Promote to L=1 levels. Validate BEFORE the promote (a Redis write)
            # so a rejected input leaves the object untouched (spec §5).
            if got_levels != 1:
                raise ValueError(f"appended index has {got_levels} levels, stored has 1")
            appended_index_labels = index_labels_per_level[0]
            _assert_scalar_encodable(appended_index_labels, "DataFrame index label")
            # Compute the honest combined level dtype: old RangeIndex values are
            # int, so the level is int64 iff the appended labels are int64-castable,
            # else object. Writing the honest dtype here means subsequent extends
            # cast against a truthful dtype instead of a stale int64 (spec §6:
            # infer, do not reject mixed). Mirrors the Series Task-5 fix.
            promote_dtype = _infer_promote_dtype(appended_index_labels)
            self._promote_df_range_to_labels(fields, owner_name, policy, level_dtype=promote_dtype)
            fields = self._raw_fields()
            index_kind = "levels"
            names = _decode_scalar(fields["~index_names"])

        # levels path (L >= 1)
        L = len(names)
        if got_levels != L:
            raise ValueError(f"appended index has {got_levels} levels, stored has {L}")
        dtypes = _decode_scalar(fields["~index_dtypes"])
        per_level_new: list[list] = []
        for i in range(L):
            col_labels = index_labels_per_level[i]
            # Validate raw labels BEFORE the dtype cast: a string-dtype cast would
            # silently stringify container labels, so scalar-encodability must be
            # confirmed first (mirrors SeriesObject.extend).
            _assert_scalar_encodable(col_labels, f"DataFrame index level {i} label")
            try:
                casted_lvl = pd.array(col_labels, dtype=dtypes[i])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Cannot extend index level {i} ({names[i]!r}) to dtype {dtypes[i]!r}: {exc}") from exc
            per_level_new.append([None if pd.isna(v) else _to_python_native(v) for v in casted_lvl])

        # One MULTI/EXEC pipeline: RPUSH each column AND each level list lockstep.
        pipe = r.pipeline(transaction=True)
        with encode_session(self._namespace):
            encoded_per_col = {
                col: [encode_element(v, self._namespace, owner_name, policy) for v in python_values_by_col[col]]
                for col in columns
            }
            encoded_levels = [
                [encode_element(v, self._namespace, owner_name, policy) for v in per_level_new[i]] for i in range(L)
            ]
        for col in columns:
            enc = encoded_per_col[col]
            if enc:
                pipe.rpush(self._get_column_list(col).raw_key, *enc)
        for i in range(L):
            if encoded_levels[i]:
                pipe.rpush(self._get_level_list(i).raw_key, *encoded_levels[i])
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
        idx = self._get_level_list(0)
        return len(idx) if idx is not None else 0

    @property
    def columns(self) -> tuple:
        return self._column_names()

    @property
    def index_names(self) -> tuple:
        return _decode_scalar(self._redis().hget(self.raw_key, "~index_names"))

    def __contains__(self, col) -> bool:
        try:
            encoded = encode_key(col)
        except TypeError:
            return False
        return bool(self._redis().hexists(self.raw_key, encoded))

    def __iter__(self):
        return iter(self._column_names())

    def __getitem__(self, col):
        """Select column(s) from the cached DataFrame.

        - ``df[col]`` (single-level, scalar) → that column as a ``pd.Series``.
        - ``df[(a, b, ...)]`` (multi-level, full-depth tuple) → that column as
          a ``pd.Series``.
        - ``df[level0]`` or ``df[(a, b)]`` (multi-level, partial prefix) → a
          sub-``pd.DataFrame`` of the matching columns.
        - ``df[list_of_keys]`` → a sub-``pd.DataFrame`` (always, even one key).

        Cost is O(N) per selected column but only over the *selected* columns
        — the rest of the DataFrame is not read — so this is the right tool
        for a one-shot selective column read (cheap: column count is small).
        For repeated or heavy work on the same data, call :attr:`value` and
        work on the local ``pandas`` object. See the module docstring.
        """
        fields = self._raw_fields()
        columns = self._column_names()  # tuple of scalars (single) or tuples (multi)
        col_names = self._columns_names_or_none()
        if isinstance(col, list):
            if col_names is not None:
                nlevels = len(col_names)
                matched = []
                missing = []
                for item in col:
                    key_tuple = item if isinstance(item, tuple) else (item,)
                    k = len(key_tuple)
                    hits = [c for c in columns if tuple(c[:k]) == key_tuple] if k <= nlevels else []
                    if hits:
                        matched.extend(hits)
                    else:
                        missing.append(item)
                # List misses raise a bare KeyError (pandas-consistent); the scalar branch
                # adds a depth message because the failure mode there is less obvious.
                if missing:
                    raise KeyError(missing)
                return self._select_columns(matched)
            missing = [c for c in col if c not in columns]
            if missing:
                raise KeyError(missing)
            return self._select_columns(col)
        if col_names is not None:
            nlevels = len(col_names)
            key_tuple = col if isinstance(col, tuple) else (col,)
            k = len(key_tuple)
            if k > nlevels:
                raise KeyError(f"column key depth {k} exceeds column levels {nlevels}")
            matched = [c for c in columns if tuple(c[:k]) == key_tuple]
            if not matched:
                raise KeyError(col)
            if k == nlevels:
                # full-depth: duplicate column tuples are rejected at create → exactly one match
                return self._select_one_series(matched[0], fields)
            return self._select_columns(matched)
        # single-level: exact scalar key
        if col not in columns:
            raise KeyError(col)
        return self._select_one_series(col, fields)

    def __setitem__(self, col, values) -> None:
        """Add or replace a column. ``len(values)`` must equal the current row count."""
        from ..lifecycle import policy_from_meta, policy_from_defaults

        r = self._redis()
        fields = self._raw_fields()
        is_multi = "~columns_names" in fields
        if is_multi:
            nlevels = len(_decode_scalar(fields["~columns_names"]))
            if not isinstance(col, tuple):
                raise TypeError(
                    "partial/list column mutation is not supported on a MultiIndex-column "
                    "DataFrame; call .value for the full pandas object and operate on it locally."
                )
            if len(col) != nlevels:
                raise ValueError(f"column key has {len(col)} levels, columns have {nlevels}")
        elif isinstance(col, (tuple, list)):
            raise TypeError(
                "tuple/list column keys are not supported on a single-level-column "
                "DataFrame; call .value for the full pandas object and operate on it locally."
            )
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

        data_values = _python_values(inferred)

        # Validate elements are scalar-encodable BEFORE releasing any old column
        # refs (a mutation), so a rejected input leaves the object untouched.
        if family in _ELEMENT_SCAN_FAMILIES:
            _assert_scalar_encodable(data_values, f"DataFrame column {col!r} element")
        if family == "category":
            _assert_scalar_encodable(
                inferred.cat.categories.tolist(),
                f"DataFrame column {col!r} category value",
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
        is_multi = "~columns_names" in fields
        if is_multi:
            nlevels = len(_decode_scalar(fields["~columns_names"]))
            if not isinstance(col, tuple):
                raise TypeError(
                    "partial/list column deletion is not supported on a MultiIndex-column "
                    "DataFrame; call .value for the full pandas object and operate on it locally."
                )
            if len(col) != nlevels:
                raise ValueError(f"column key has {len(col)} levels, columns have {nlevels}")
        elif isinstance(col, (tuple, list)):
            raise TypeError(
                "tuple/list column keys are not supported on a single-level-column "
                "DataFrame; call .value for the full pandas object and operate on it locally."
            )
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
        """Row label accessor (depth-k prefix on MultiIndex rows).

        O(N) selective scan — fine for a one-shot partial read, not for hot
        loops; for repeated lookups call :attr:`value`. Slices and label-lists
        are rejected (call :attr:`value`). Column selection uses
        ``df[...]`` (cheap; few columns). See the module docstring.
        """
        return _DfLocIndexer(self)

    def _positional_row_slice(self, sl: slice) -> pd.DataFrame:
        columns = self._column_names()
        fields = self._raw_fields()
        kind = _decode_scalar(fields["~index_kind"])
        if kind == "range":
            index_obj = _build_index(fields, self._namespace, prefix="~")[sl]
        else:
            names = _decode_scalar(fields["~index_names"])
            arrays = [self._get_level_list(i)[sl] for i in range(len(names))]
            if len(names) == 1:
                index_obj = pd.Index(arrays[0], name=names[0])
            else:
                index_obj = pd.MultiIndex.from_arrays(arrays, names=names)
        data_dict = {}
        for col in columns:
            col_list = self._get_column_list(col)
            values = col_list[sl] if col_list is not None else []
            data_dict[col] = _reconstruct_column(values, index_obj, fields, col, self._namespace)
        return pd.DataFrame(data_dict, columns=_build_columns_index(fields, columns))


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
        categories = value.cat.categories.tolist() if family == "category" else None

        if family in _ELEMENT_SCAN_FAMILIES:
            _assert_scalar_encodable(data_values, "Series element")
        if categories is not None:
            _assert_scalar_encodable(categories, "Series category value")

        fields: dict[str, str] = {}
        with encode_session(namespace):
            fields["data"] = encode_element(data_values, namespace, owner_name, policy)
            if family == "category":
                fields["categories"] = encode_element(categories, namespace, owner_name, policy)
            if kind == "levels":
                # Bind all level label lists to locals before encoding (id-aliasing guard).
                level_labels = [_level_labels_and_dtype(index, i) for i in range(index.nlevels)]
                for i, (labels, _dtype) in enumerate(level_labels):
                    _assert_scalar_encodable(labels, f"Series index level {i} label")
                    fields[f"index_l{i}"] = encode_element(labels, namespace, owner_name, policy)

        fields["dtype"] = _encode_extra_value(str(value.dtype))
        fields["name"] = _encode_extra_value(value.name)
        fields["index_kind"] = _encode_extra_value(kind)
        fields["index_names"] = _encode_extra_value(tuple(index.names))
        if kind == "levels":
            fields["index_dtypes"] = _encode_extra_value(tuple(dt for _, dt in level_labels))
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
        index_kind = _decode_scalar(fields["index_kind"])
        if index_kind == "range":
            index_obj = pd.RangeIndex(
                _decode_scalar(fields["range_start"]),
                _decode_scalar(fields["range_stop"]),
                _decode_scalar(fields["range_step"]),
                name=_decode_scalar(fields["index_names"])[0],
            )
        else:  # levels
            index_obj = _build_levels_index(fields, namespace)
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
        raise TypeError(
            ".iloc list (positional fancy) indexing is not supported on this cache object; "
            "use .iloc with an int or slice, or call .value for the full pandas object."
        )


class _LocIndexer:
    def __init__(self, owner: "SeriesObject") -> None:
        self._owner = owner

    def __getitem__(self, key):
        owner = self._owner
        if isinstance(key, slice):
            raise TypeError(
                ".loc slices are not supported on this cache object; "
                "call .value for the full pandas object and operate on it locally, "
                "or use .iloc for positional slicing."
            )
        if isinstance(key, list):
            raise TypeError(
                ".loc label-list selection is not supported on this cache object; "
                "call .value for the full pandas object and operate on it locally."
            )
        fields = owner._raw_fields()
        if _decode_scalar(fields["index_kind"]) == "range":
            raise TypeError(
                ".loc on a RangeIndex is not supported on this cache object; "
                "use .iloc for positional slicing, or call .value for the full pandas object."
            )
        names = _decode_scalar(fields["index_names"])
        L = len(names)
        # normalise key to a depth-k tuple
        key_tuple = key if isinstance(key, tuple) else (key,)
        k = len(key_tuple)
        if k > L:
            raise KeyError(f"key depth {k} exceeds index levels {L}")
        # Fetch all L level values once (ListObject.value is uncached -> each call is a Redis lrange).
        all_level_values = [owner._get_level_list(i).value for i in range(L)]
        # NA-label matching follows Python == semantics; nan labels won't match (pre-existing behaviour).
        positions = [
            pos for pos in range(len(owner)) if all(all_level_values[i][pos] == key_tuple[i] for i in range(k))
        ]
        if not positions:
            raise KeyError(key)
        # full-depth + exactly one match -> scalar value (pandas semantics)
        if k == L and len(positions) == 1:
            return owner._get_data_list()[positions[0]]
        data = owner._get_data_list()
        values = [data[p] for p in positions]
        # Build the result index from the SAME all_level_values (matched positions, all L levels).
        arrays = [[lvl[p] for p in positions] for lvl in all_level_values]
        if L == 1:
            sliced_index = pd.Index(arrays[0], name=names[0])
        else:
            sliced_index = pd.MultiIndex.from_arrays(arrays, names=names)
        return owner._build_series_from(values, sliced_index)


class SeriesObject(GPDCContainerObject):
    TYPE = "collection:pandas_series"

    def __init__(self, name, namespace, *, canonical_name=None) -> None:
        super().__init__(name, namespace, canonical_name=canonical_name)
        self._data_list_cache = _UNLOADED
        self._level_lists_cache: dict = {}

    @property
    def type(self) -> str:
        return self.TYPE

    @property
    def value(self) -> pd.Series:
        """Full materialization as a plain :class:`pandas.Series`.

        Use this for repeated or heavy work on the same data (sorting, many
        label lookups, complex pandas semantics, or operations the online API
        rejects). For a one-shot selective read, prefer :attr:`iloc` / :attr:`loc`.
        See the module docstring for the full usage guidance.
        """
        return SeriesCodec.read_raw(self._namespace, self.raw_key)

    @value.setter
    def value(self, new_value) -> None:
        self._reject_value_replacement()

    def _get_data_list(self):
        if self._data_list_cache is _UNLOADED:
            ref = self._redis().hget(self.raw_key, "data")
            self._data_list_cache = decode_element(ref, self._namespace) if ref else None
        return self._data_list_cache

    def _get_level_list(self, level_i: int):
        """Return the per-level index child ListObject, or None when the level is absent."""
        if level_i not in self._level_lists_cache:
            ref = self._redis().hget(self.raw_key, f"index_l{level_i}")
            self._level_lists_cache[level_i] = decode_element(ref, self._namespace) if ref else None
        return self._level_lists_cache[level_i]

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
    def index_names(self) -> tuple:
        return _decode_scalar(self._redis().hget(self.raw_key, "index_names"))

    def deepcopy(self) -> pd.Series:
        return self.value.copy(deep=True)

    def _current_item_count(self) -> int:
        return len(self)

    @property
    def iloc(self) -> "_ILocIndexer":
        return _ILocIndexer(self)

    @property
    def loc(self) -> "_LocIndexer":
        """Row label accessor (depth-k prefix on a MultiIndex).

        O(N) selective scan — fine for a one-shot partial read, but do not use
        in a hot loop; for repeated lookups call :attr:`value` and work on the
        local ``pandas`` object. Slices and label-lists are rejected (call
        :attr:`value`). See the module docstring.
        """
        return _LocIndexer(self)

    def _positional_index_slice(self, sl: slice):
        """Return the pd.Index for a positional slice, honoring RangeIndex and MultiIndex."""
        fields = self._raw_fields()
        index_kind = _decode_scalar(fields["index_kind"])
        names = _decode_scalar(fields["index_names"])
        if index_kind == "range":
            full = pd.RangeIndex(
                _decode_scalar(fields["range_start"]),
                _decode_scalar(fields["range_stop"]),
                _decode_scalar(fields["range_step"]),
                name=names[0],
            )
            return full[sl]
        # levels: slice each level list
        arrays = [self._get_level_list(i)[sl] for i in range(len(names))]
        if len(names) == 1:
            return pd.Index(arrays[0], name=names[0])
        return pd.MultiIndex.from_arrays(arrays, names=names)

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
        index_kind = _decode_scalar(fields["index_kind"])
        names = _decode_scalar(fields["index_names"])

        labels, data_values, got_levels = _normalize_extend_input(other)

        try:
            casted = pd.Series(data_values, dtype=dtype_str)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Cannot extend Series of dtype {dtype_str!r} with given values: {exc}") from exc
        new_data = _python_values(casted)

        # Validate elements are scalar-encodable BEFORE any Redis write (promote/encode)
        # so a rejected input leaves the cached object untouched.
        series_family = _value_dtype_family(pd.api.types.pandas_dtype(dtype_str))
        if series_family in _ELEMENT_SCAN_FAMILIES:
            _assert_scalar_encodable(new_data, "Series element")

        r = self._redis()
        meta = ObjectMeta.load(r, self._canonical_redis_key())
        policy = policy_from_meta(meta) if meta else policy_from_defaults("permanent", 0)
        owner_name = self._data_owner_name()

        if index_kind == "range":
            # Range path: promote to L=1 levels if the appended input isn't range-compatible,
            # else just bump range_stop. Mirror the pre-existing range behaviour.
            if not _is_range_indexed_series(other):
                # A range Series promotes to L=1 levels. Validate the appended input
                # BEFORE the promote (a Redis write) so a rejected input leaves the
                # object untouched (spec §5).
                if got_levels != 1:
                    raise ValueError(f"appended index has {got_levels} levels, stored has 1")
                appended_index_labels = [lbl[0] for lbl in labels]
                _assert_scalar_encodable(appended_index_labels, "Series index label")
                # Compute the honest combined level dtype: old RangeIndex values are int,
                # so the level is int64 iff the appended labels are int64-castable, else
                # object. Writing the honest dtype here means subsequent extends cast
                # against a truthful dtype instead of a stale int64 (spec §6: infer, do
                # not reject mixed).
                promote_dtype = _infer_promote_dtype(appended_index_labels)
                self._promote_range_to_labels(fields, owner_name, policy, level_dtype=promote_dtype)
                fields = self._raw_fields()
                names = _decode_scalar(fields["index_names"])
            else:
                data_list = self._get_data_list()
                new_stop = _decode_scalar(fields["range_stop"]) + len(new_data)
                pipe = r.pipeline(transaction=True)
                with encode_session(self._namespace):
                    encoded_data = [encode_element(v, self._namespace, owner_name, policy) for v in new_data]
                if encoded_data:
                    pipe.rpush(data_list.raw_key, *encoded_data)
                pipe.hset(self.raw_key, "range_stop", _encode_extra_value(new_stop))
                pipe.execute()
                self._data_list_cache = _UNLOADED
                self._level_lists_cache = {}
                self._touch()
                return

        # levels path (L >= 1)
        L = len(names)
        if got_levels != L:
            raise ValueError(f"appended index has {got_levels} levels, stored has {L}")
        dtypes = _decode_scalar(fields["index_dtypes"])
        per_level_new: list[list] = []
        for i in range(L):
            col_labels = [lbl[i] for lbl in labels]
            # Validate raw labels BEFORE the dtype cast: a string-dtype cast would
            # silently stringify container labels (e.g. [1, 2] -> "[1, 2]"), so the
            # cast must not run before scalar-encodability is confirmed. The promote
            # branch already validated L=0 labels; this re-check is a harmless no-op
            # for that path but required for the direct levels path.
            _assert_scalar_encodable(col_labels, f"Series index level {i} label")
            try:
                casted_lvl = pd.array(col_labels, dtype=dtypes[i])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Cannot extend index level {i} ({names[i]!r}) to dtype {dtypes[i]!r}: {exc}") from exc
            per_level_new.append([None if pd.isna(v) else _to_python_native(v) for v in casted_lvl])

        data_list = self._get_data_list()
        pipe = r.pipeline(transaction=True)
        with encode_session(self._namespace):
            encoded_data = [encode_element(v, self._namespace, owner_name, policy) for v in new_data]
            encoded_levels = [
                [encode_element(v, self._namespace, owner_name, policy) for v in per_level_new[i]] for i in range(L)
            ]
        if encoded_data:
            pipe.rpush(data_list.raw_key, *encoded_data)
        for i in range(L):
            if encoded_levels[i]:
                pipe.rpush(self._get_level_list(i).raw_key, *encoded_levels[i])
        pipe.execute()
        self._data_list_cache = _UNLOADED
        self._level_lists_cache = {}
        self._touch()

    def _promote_range_to_labels(self, fields: dict, owner_name, policy, level_dtype="int64") -> None:
        start = _decode_scalar(fields["range_start"])
        stop = _decode_scalar(fields["range_stop"])
        step = _decode_scalar(fields["range_step"])
        labels = list(pd.RangeIndex(start, stop, step))
        with encode_session(self._namespace):
            ref = encode_element(labels, self._namespace, owner_name, policy)
        r = self._redis()
        mapping = {
            "index_l0": ref,
            "index_kind": _encode_extra_value("levels"),
            "index_dtypes": _encode_extra_value((level_dtype,)),
        }
        for k in ("range_start", "range_stop", "range_step"):
            if k in fields:
                r.hdel(self.raw_key, k)
        r.hset(self.raw_key, mapping=mapping)
        self._level_lists_cache = {}
