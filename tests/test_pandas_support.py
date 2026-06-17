from __future__ import annotations

import threading

import pandas as pd
import pytest

from gpdatacached.types import GPDCTypeRegistry


@pytest.fixture(autouse=True)
def _register_series():
    """Isolate registry and register the pandas Series type for every test."""
    from gpdatacached.extensions.pandas_support import register

    original_gpdc = dict(GPDCTypeRegistry._by_gpdc_type)
    original_python = list(GPDCTypeRegistry._by_python_type)
    original_lock = GPDCTypeRegistry._lock
    try:
        GPDCTypeRegistry._lock = threading.Lock()
        GPDCTypeRegistry._by_gpdc_type = dict(original_gpdc)
        GPDCTypeRegistry._by_python_type = list(original_python)
        register()
        yield
    finally:
        GPDCTypeRegistry._by_gpdc_type = original_gpdc
        GPDCTypeRegistry._by_python_type = original_python
        GPDCTypeRegistry._lock = original_lock


class TestDtypeClassification:
    @pytest.mark.parametrize(
        "dtype_str", ["int64", "Int64", "float64", "Float64", "bool", "boolean", "object", "string"]
    )
    def test_supported_value_dtypes(self, dtype_str):
        from gpdatacached.extensions.pandas_support import _value_dtype_family

        dtype = pd.api.types.pandas_dtype(dtype_str)
        assert _value_dtype_family(dtype) is not None

    @pytest.mark.parametrize("dtype_str", ["timedelta64[ns]", "complex128"])
    def test_unsupported_value_dtypes(self, dtype_str):
        from gpdatacached.extensions.pandas_support import _value_dtype_family

        dtype = pd.api.types.pandas_dtype(dtype_str)
        assert _value_dtype_family(dtype) is None

    def test_category_supported(self):
        from gpdatacached.extensions.pandas_support import _value_dtype_family

        dtype = pd.CategoricalDtype(["a", "b"])
        assert _value_dtype_family(dtype) == "category"

    def test_tz_aware_datetime_supported(self):
        from gpdatacached.extensions.pandas_support import _value_dtype_family

        dtype = pd.api.types.pandas_dtype("datetime64[ns, UTC]")
        assert _value_dtype_family(dtype) == "datetime"

    def test_unsupported_dtype_error_message_guides_user(self):
        from gpdatacached.extensions.pandas_support import UnsupportedSeriesDtypeError

        with pytest.raises(UnsupportedSeriesDtypeError) as exc_info:
            raise UnsupportedSeriesDtypeError("timedelta64[ns]", is_index=False)
        msg = str(exc_info.value)
        assert "timedelta64[ns]" in msg
        assert "GPDCTypeRegistry.register" in msg


class TestSeriesCodecRoundTrip:
    def test_int64_labeled_index_round_trip(self, ns):
        from gpdatacached.extensions.pandas_support import SeriesCodec

        original = pd.Series([10, 20, 30], index=["a", "b", "c"], name="scores")
        raw_key = "gpdc:testdomain:testns:raw:s1"
        try:
            SeriesCodec.create_raw(ns, raw_key, original, "owner", "permanent", 0)
            result = SeriesCodec.read_raw(ns, raw_key)
        finally:
            ns._redis().delete(raw_key)

        assert isinstance(result, pd.Series)
        assert list(result.values) == [10, 20, 30]
        assert list(result.index) == ["a", "b", "c"]
        assert result.name == "scores"
        assert str(result.dtype) == "int64"

    def test_raw_hash_contains_data_and_index_refs(self, ns):
        from gpdatacached.extensions.pandas_support import SeriesCodec

        original = pd.Series([1, 2], index=["x", "y"])
        raw_key = "gpdc:testdomain:testns:raw:s2"
        try:
            SeriesCodec.create_raw(ns, raw_key, original, "owner", "permanent", 0)
            data_ref = ns._redis().hget(raw_key, "data")
            index_ref = ns._redis().hget(raw_key, "index")
            assert data_ref.startswith("ref:")
            assert index_ref.startswith("ref:")
            assert data_ref != index_ref
            assert ns._redis().hget(raw_key, "dtype") == "str:int64"
        finally:
            ns._redis().delete(raw_key)


class TestAllValueDtypes:
    @pytest.mark.parametrize(
        "series",
        [
            pd.Series([1, 2, 3]),  # int64
            pd.Series([1, 2, 3], dtype="Int64"),  # nullable int
            pd.Series([1.5, 2.5]),  # float64
            pd.Series([1.5, 2.5], dtype="Float64"),  # nullable float
            pd.Series([True, False, True]),  # bool
            pd.Series([True, False], dtype="boolean"),  # nullable bool
            pd.Series(["a", "b"]),  # object str
            pd.Series(["a", "b"], dtype="string"),  # string dtype
            pd.Series(pd.to_datetime(["2020-01-01", "2021-01-01"])),  # datetime naive
            pd.Series(pd.to_datetime(["2020-01-01", "2021-01-01"], utc=True)),  # datetime tz-aware
            pd.Series(["x", "y", "x"], dtype="category"),  # category
            pd.Series([{"k": 1}, [1, 2], "s"]),  # object mixed
        ],
    )
    def test_round_trip_preserves_dtype_values_index(self, ns, series):
        from gpdatacached.extensions.pandas_support import SeriesCodec

        raw_key = f"gpdc:testdomain:testns:raw:rt-{id(series)}"
        try:
            SeriesCodec.create_raw(ns, raw_key, series, "owner", "permanent", 0)
            result = SeriesCodec.read_raw(ns, raw_key)
        finally:
            ns._redis().delete(raw_key)

        assert str(result.dtype) == str(series.dtype)
        assert list(result.values) == list(series.values)
        assert list(result.index) == list(series.index)

    def test_category_preserves_categories_and_ordered(self, ns):
        from gpdatacached.extensions.pandas_support import SeriesCodec

        series = pd.Series(["b", "a", "c"], dtype=pd.CategoricalDtype(["a", "b", "c"], ordered=True))
        raw_key = "gpdc:testdomain:testns:raw:cat"
        try:
            SeriesCodec.create_raw(ns, raw_key, series, "owner", "permanent", 0)
            result = SeriesCodec.read_raw(ns, raw_key)
        finally:
            ns._redis().delete(raw_key)

        assert list(result.cat.categories) == ["a", "b", "c"]
        assert result.cat.ordered is True

    def test_tz_aware_preserves_timezone(self, ns):
        from gpdatacached.extensions.pandas_support import SeriesCodec

        series = pd.Series(pd.to_datetime(["2020-01-01", "2021-01-01"], utc=True))
        raw_key = "gpdc:testdomain:testns:raw:tz"
        try:
            SeriesCodec.create_raw(ns, raw_key, series, "owner", "permanent", 0)
            result = SeriesCodec.read_raw(ns, raw_key)
        finally:
            ns._redis().delete(raw_key)
        # pandas 3.x defaults tz-aware datetimes to microsecond resolution
        # (datetime64[us, UTC]) instead of nanosecond; assert tz is preserved
        # rather than hardcoding the resolution string.
        assert result.dtype == series.dtype
        assert str(result.dtype.tz) == "UTC"


class TestMissingValues:
    def test_float_nan_round_trip(self, ns):
        from gpdatacached.extensions.pandas_support import SeriesCodec

        series = pd.Series([1.0, float("nan"), 3.0])
        raw_key = "gpdc:testdomain:testns:raw:nan"
        try:
            SeriesCodec.create_raw(ns, raw_key, series, "owner", "permanent", 0)
            result = SeriesCodec.read_raw(ns, raw_key)
        finally:
            ns._redis().delete(raw_key)
        assert pd.isna(result.iloc[1])
        assert result.iloc[0] == 1.0 and result.iloc[2] == 3.0

    def test_nullable_int_na_round_trip(self, ns):
        from gpdatacached.extensions.pandas_support import SeriesCodec

        series = pd.Series([1, None, 3], dtype="Int64")
        raw_key = "gpdc:testdomain:testns:raw:intna"
        try:
            SeriesCodec.create_raw(ns, raw_key, series, "owner", "permanent", 0)
            result = SeriesCodec.read_raw(ns, raw_key)
        finally:
            ns._redis().delete(raw_key)
        assert str(result.dtype) == "Int64"
        assert pd.isna(result.iloc[1])

    def test_datetime_nat_round_trip(self, ns):
        from gpdatacached.extensions.pandas_support import SeriesCodec

        series = pd.Series(pd.to_datetime(["2020-01-01", None]))
        raw_key = "gpdc:testdomain:testns:raw:nat"
        try:
            SeriesCodec.create_raw(ns, raw_key, series, "owner", "permanent", 0)
            result = SeriesCodec.read_raw(ns, raw_key)
        finally:
            ns._redis().delete(raw_key)
        assert pd.isna(result.iloc[1])

    def test_object_none_round_trip(self, ns):
        from gpdatacached.extensions.pandas_support import SeriesCodec

        series = pd.Series(["a", None, "c"], dtype=object)
        raw_key = "gpdc:testdomain:testns:raw:none"
        try:
            SeriesCodec.create_raw(ns, raw_key, series, "owner", "permanent", 0)
            result = SeriesCodec.read_raw(ns, raw_key)
        finally:
            ns._redis().delete(raw_key)
        assert result.iloc[1] is None


class TestRangeIndex:
    def test_range_index_omits_index_child(self, ns):
        from gpdatacached.extensions.pandas_support import SeriesCodec

        series = pd.Series([10, 20, 30])  # default RangeIndex 0..3 step 1
        raw_key = "gpdc:testdomain:testns:raw:rng"
        try:
            SeriesCodec.create_raw(ns, raw_key, series, "owner", "permanent", 0)
            assert ns._redis().hget(raw_key, "index") is None
            assert ns._redis().hget(raw_key, "index_kind") == "str:range"
        finally:
            ns._redis().delete(raw_key)

    def test_range_index_round_trip(self, ns):
        from gpdatacached.extensions.pandas_support import SeriesCodec

        series = pd.Series([10, 20, 30], name="n")
        raw_key = "gpdc:testdomain:testns:raw:rng2"
        try:
            SeriesCodec.create_raw(ns, raw_key, series, "owner", "permanent", 0)
            result = SeriesCodec.read_raw(ns, raw_key)
        finally:
            ns._redis().delete(raw_key)
        assert list(result.index) == [0, 1, 2]
        assert list(result.values) == [10, 20, 30]

    def test_custom_step_range_index_round_trip(self, ns):
        from gpdatacached.extensions.pandas_support import SeriesCodec

        series = pd.Series([10, 20, 30], index=range(5, 20, 5))
        raw_key = "gpdc:testdomain:testns:raw:rng3"
        try:
            SeriesCodec.create_raw(ns, raw_key, series, "owner", "permanent", 0)
            result = SeriesCodec.read_raw(ns, raw_key)
        finally:
            ns._redis().delete(raw_key)
        assert list(result.index) == [5, 10, 15]


class TestIndexVariety:
    def test_duplicate_labels_round_trip(self, ns):
        from gpdatacached.extensions.pandas_support import SeriesCodec

        series = pd.Series([1, 2, 3], index=["a", "a", "b"])
        raw_key = "gpdc:testdomain:testns:raw:dup"
        try:
            SeriesCodec.create_raw(ns, raw_key, series, "owner", "permanent", 0)
            result = SeriesCodec.read_raw(ns, raw_key)
        finally:
            ns._redis().delete(raw_key)
        assert list(result.index) == ["a", "a", "b"]
        assert list(result.values) == [1, 2, 3]

    def test_integer_label_index_round_trip(self, ns):
        from gpdatacached.extensions.pandas_support import SeriesCodec

        series = pd.Series([10, 20], index=[100, 200])
        raw_key = "gpdc:testdomain:testns:raw:iidx"
        try:
            SeriesCodec.create_raw(ns, raw_key, series, "owner", "permanent", 0)
            result = SeriesCodec.read_raw(ns, raw_key)
        finally:
            ns._redis().delete(raw_key)
        assert list(result.index) == [100, 200]

    def test_datetime_label_index_round_trip(self, ns):
        from gpdatacached.extensions.pandas_support import SeriesCodec

        idx = pd.to_datetime(["2020-01-01", "2020-01-02"])
        series = pd.Series([1.0, 2.0], index=idx)
        raw_key = "gpdc:testdomain:testns:raw:didx"
        try:
            SeriesCodec.create_raw(ns, raw_key, series, "owner", "permanent", 0)
            result = SeriesCodec.read_raw(ns, raw_key)
        finally:
            ns._redis().delete(raw_key)
        assert list(result.index) == list(idx)

    def test_multiindex_is_unsupported(self, ns):
        from gpdatacached.extensions.pandas_support import SeriesCodec, UnsupportedSeriesDtypeError

        series = pd.Series([1, 2], index=pd.MultiIndex.from_tuples([("a", 1), ("a", 2)]))
        raw_key = "gpdc:testdomain:testns:raw:mi"
        with pytest.raises(UnsupportedSeriesDtypeError):
            SeriesCodec.create_raw(ns, raw_key, series, "owner", "permanent", 0)
