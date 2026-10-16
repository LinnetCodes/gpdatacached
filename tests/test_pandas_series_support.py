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
            index_ref = ns._redis().hget(raw_key, "index_l0")
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


class TestRangeIndexNonUnitStepLogic03:
    """CQR-2026-07-24 logic.03: non-unit (and negative) RangeIndex step must be
    honoured by __len__, extend, and .iloc — not just round-trip."""

    def test_len_non_unit_step(self, ns):
        ns["s"] = pd.Series([10, 20, 30], index=pd.RangeIndex(5, 20, 5))
        obj = ns.get_object("s")
        assert len(obj) == 3  # not 15 (= stop - start)

    def test_len_negative_step(self, ns):
        ns["s"] = pd.Series([30, 20, 10], index=pd.RangeIndex(20, 5, -5))
        obj = ns.get_object("s")
        assert len(obj) == 3  # range(20, 5, -5) -> [20, 15, 10]

    def test_extend_non_unit_step_advances_stop_by_len_times_step(self, ns):
        ns["s"] = pd.Series([10, 20, 30], index=pd.RangeIndex(5, 20, 5), name="x")
        obj = ns.get_object("s")
        obj.extend(pd.Series([40, 50], index=pd.RangeIndex(20, 30, 5), name="x"))
        val = obj.value
        assert len(val) == 5
        assert list(val.index) == [5, 10, 15, 20, 25]

    def test_extend_negative_step_advances_stop_by_len_times_step(self, ns):
        # range(20, 5, -5) = [20,15,10]; extend 2 -> [20,15,10,5,0], stop -> -5... wait
        # range(20, 5, -5) has stop 5; appending 2 with step -5 -> new_stop = 5 + 2*(-5) = -5
        # giving range(20, -5, -5) = [20,15,10,5,0]. 5 values.
        ns["s"] = pd.Series([30, 20, 10], index=pd.RangeIndex(20, 5, -5), name="x")
        obj = ns.get_object("s")
        obj.extend(pd.Series([5, 0], index=pd.RangeIndex(5, -5, -5), name="x"))
        val = obj.value
        assert len(val) == 5
        assert list(val.index) == [20, 15, 10, 5, 0]

    def test_extend_repeated_non_unit_step(self, ns):
        # Two successive extends on a non-unit range stay consistent.
        ns["s"] = pd.Series([10, 20, 30], index=pd.RangeIndex(5, 20, 5), name="x")
        obj = ns.get_object("s")
        obj.extend(pd.Series([40], index=pd.RangeIndex(20, 25, 5), name="x"))
        obj.extend(pd.Series([50, 60], index=pd.RangeIndex(25, 35, 5), name="x"))
        val = obj.value
        assert list(val.index) == [5, 10, 15, 20, 25, 30]

    def test_iloc_slice_non_unit_step(self, ns):
        ns["s"] = pd.Series([10, 20, 30], index=pd.RangeIndex(5, 20, 5))
        obj = ns.get_object("s")
        sub = obj.iloc[0:2]
        assert list(sub.index) == [5, 10]
        assert list(sub.values) == [10, 20]

    def test_non_unit_step_value_round_trip(self, ns):
        ns["s"] = pd.Series([10, 20, 30], index=pd.RangeIndex(5, 20, 5), name="x")
        obj = ns.get_object("s")
        assert list(obj.value.index) == [5, 10, 15]
        assert obj.series_name == "x"


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

    def test_multiindex_series_now_supported(self, ns):
        original = pd.Series(
            [10, 20, 30],
            index=pd.MultiIndex.from_tuples([("a", 1), ("a", 2), ("b", 1)], names=["L0", "L1"]),
            name="s",
        )
        ns["ms"] = original
        result = ns["ms"].value
        assert isinstance(result.index, pd.MultiIndex)
        assert list(result.index) == list(original.index)
        assert result.index.names == ("L0", "L1")
        assert list(result.values) == [10, 20, 30]

    def test_sparse_level_rejected(self, ns):
        from gpdatacached.extensions.pandas_support import UnsupportedSeriesDtypeError

        idx = pd.MultiIndex.from_arrays([["a", "b"], pd.array([1, 2], dtype="Sparse[int64]")])
        with pytest.raises(UnsupportedSeriesDtypeError):
            ns["bad"] = pd.Series([1, 2], index=idx)


class TestSeriesMultiIndexStorage:
    def test_two_level_round_trip_preserves_levels_and_dtypes(self, ns):
        from gpdatacached.extensions.pandas_support import SeriesCodec

        idx = pd.MultiIndex.from_arrays([["a", "a", "b"], [1, 2, 1]], names=["L0", "L1"])
        original = pd.Series([10, 20, 30], index=idx, name="s")
        raw_key = "gpdc:testdomain:testns:raw:mi"
        try:
            SeriesCodec.create_raw(ns, raw_key, original, "owner", "permanent", 0)
            result = SeriesCodec.read_raw(ns, raw_key)
        finally:
            ns._redis().delete(raw_key)
        assert isinstance(result.index, pd.MultiIndex)
        assert list(result.index) == list(original.index)
        assert result.index.names == ("L0", "L1")
        assert str(result.index.get_level_values(1).dtype) == "int64"
        assert list(result.values) == [10, 20, 30]

    def test_raw_hash_has_per_level_ref_fields(self, ns):
        from gpdatacached.extensions.pandas_support import SeriesCodec

        idx = pd.MultiIndex.from_arrays([["a", "b"], [1, 2]], names=["L0", "L1"])
        original = pd.Series([1, 2], index=idx)
        raw_key = "gpdc:testdomain:testns:raw:mihash"
        try:
            SeriesCodec.create_raw(ns, raw_key, original, "owner", "permanent", 0)
            assert ns._redis().hget(raw_key, "index_kind") == "str:levels"
            assert ns._redis().hget(raw_key, "index_l0").startswith("ref:")
            assert ns._redis().hget(raw_key, "index_l1").startswith("ref:")
            assert ns._redis().hget(raw_key, "index_l0") != ns._redis().hget(raw_key, "index_l1")
            assert ns._redis().hget(raw_key, "index_names").startswith("tuple:")
            assert ns._redis().hget(raw_key, "index_dtypes").startswith("tuple:")
        finally:
            ns._redis().delete(raw_key)

    def test_l1_levels_equivalent_to_legacy_labels(self, ns):
        from gpdatacached.extensions.pandas_support import SeriesCodec

        original = pd.Series([1, 2, 3], index=["x", "y", "z"], name="s")
        raw_key = "gpdc:testdomain:testns:raw:l1"
        try:
            SeriesCodec.create_raw(ns, raw_key, original, "owner", "permanent", 0)
            result = SeriesCodec.read_raw(ns, raw_key)
        finally:
            ns._redis().delete(raw_key)
        assert list(result.index) == ["x", "y", "z"]
        assert result.name == "s"


class TestSeriesObjectCore:
    def test_store_and_get_value(self, ns):
        series = pd.Series([1, 2, 3], index=["a", "b", "c"], name="n")
        ns["s"] = series
        obj = ns.get_object("s")
        assert obj.type == "collection:pandas_series"
        assert list(obj.value.values) == [1, 2, 3]
        assert list(obj.value.index) == ["a", "b", "c"]

    def test_len_dtype_name_index_names(self, ns):
        # Binding name "s" deliberately differs from pandas name "vals".
        # .name is the GPDC binding (base-class identity contract, same as
        # DataFrameObject); .series_name is the pandas payload label.
        ns["s"] = pd.Series([1, 2, 3], index=["a", "b", "c"], name="vals")
        obj = ns.get_object("s")
        assert len(obj) == 3
        assert obj.dtype == "int64"
        assert obj.name == "s"  # binding name, not the pandas label
        assert obj.series_name == "vals"  # the pandas Series.name
        assert obj.index_names == (None,)

    def test_name_is_binding_identity(self, ns):
        # Regression guard for CQR-2026-07-24 inconsistency.02: SeriesObject.name
        # must agree with .uri and .canonical_name (the base-class identity
        # contract), matching every other object type incl. DataFrameObject.
        ns["binding"] = pd.Series([1, 2, 3], name="payload_name")
        obj = ns.get_object("binding")
        assert obj.name == "binding"
        assert obj.canonical_name == "binding"
        assert obj.uri.endswith("/binding")
        assert obj.series_name == "payload_name"

    def test_index_names_is_tuple(self, ns):
        ns["s"] = pd.Series([1, 2], index=["a", "b"], name="s")
        assert ns["s"].index_names == (None,)
        ns["s2"] = pd.Series([1, 2], index=pd.Index(["a", "b"], name="lvl0"))
        assert ns["s2"].index_names == ("lvl0",)

    def test_deepcopy_returns_plain_series(self, ns):
        ns["s"] = pd.Series([1, 2], index=["a", "b"], name="n")
        obj = ns.get_object("s")
        cp = obj.deepcopy()
        assert isinstance(cp, pd.Series)
        assert list(cp.values) == [1, 2]

    def test_value_setter_rejected(self, ns):
        ns["s"] = pd.Series([1, 2])
        obj = ns.get_object("s")
        with pytest.raises(TypeError):
            obj.value = pd.Series([9, 9])


class TestILoc:
    def test_iloc_int_returns_scalar(self, ns):
        ns["s"] = pd.Series([10, 20, 30], index=["a", "b", "c"])
        obj = ns.get_object("s")
        assert obj.iloc[0] == 10
        assert obj.iloc[-1] == 30

    def test_iloc_numpy_integer_accepted(self, ns):
        import numpy as np

        ns["s"] = pd.Series([10, 20, 30], index=["a", "b", "c"])
        obj = ns.get_object("s")
        assert obj.iloc[np.int64(1)] == 20
        assert obj.iloc[np.intp(2)] == 30

    def test_iloc_slice_returns_series(self, ns):
        ns["s"] = pd.Series([10, 20, 30, 40], index=["a", "b", "c", "d"], name="n")
        obj = ns.get_object("s")
        sub = obj.iloc[1:3]
        assert isinstance(sub, pd.Series)
        assert list(sub.values) == [20, 30]
        assert list(sub.index) == ["b", "c"]
        assert sub.name == "n"

    def test_iloc_on_range_index_series(self, ns):
        ns["s"] = pd.Series([10, 20, 30])
        obj = ns.get_object("s")
        assert obj.iloc[2] == 30
        sub = obj.iloc[0:2]
        assert list(sub.index) == [0, 1]
        assert list(sub.values) == [10, 20]


class TestLoc:
    def test_loc_returns_single_match(self, ns):
        ns["s"] = pd.Series([10, 20, 30], index=["a", "b", "c"])
        obj = ns.get_object("s")
        result = obj.loc["b"]
        assert result == 20

    def test_loc_duplicate_labels_returns_all(self, ns):
        ns["s"] = pd.Series([1, 2, 3], index=["a", "a", "b"])
        obj = ns.get_object("s")
        result = obj.loc["a"]
        assert list(result.values) == [1, 2]
        assert list(result.index) == ["a", "a"]

    def test_loc_missing_label_raises(self, ns):
        ns["s"] = pd.Series([1, 2], index=["a", "b"])
        obj = ns.get_object("s")
        with pytest.raises(KeyError):
            obj.loc["z"]

    def test_loc_on_range_index_raises(self, ns):
        ns["s"] = pd.Series([1, 2, 3])
        obj = ns.get_object("s")
        with pytest.raises(TypeError):
            obj.loc[1]


class TestSeriesMultiIndexRead:
    def _setup(self, ns):
        idx = pd.MultiIndex.from_tuples([("a", 1), ("a", 2), ("b", 1)], names=["L0", "L1"])
        ns["ms"] = pd.Series([10, 20, 30], index=idx)
        return ns["ms"]

    def test_iloc_int_returns_scalar(self, ns):
        obj = self._setup(ns)
        assert obj.iloc[0] == 10
        assert obj.iloc[2] == 30

    def test_iloc_slice_returns_series_with_multiindex(self, ns):
        obj = self._setup(ns)
        result = obj.iloc[0:2]
        assert list(result.values) == [10, 20]
        assert isinstance(result.index, pd.MultiIndex)
        assert list(result.index) == [("a", 1), ("a", 2)]

    def test_loc_full_tuple_one_match_returns_scalar(self, ns):
        obj = self._setup(ns)
        assert obj.loc[("a", 1)] == 10
        assert obj.loc[("b", 1)] == 30

    def test_loc_depth1_prefix_returns_series(self, ns):
        obj = self._setup(ns)
        result = obj.loc["a"]
        assert list(result.values) == [10, 20]
        assert list(result.index) == [("a", 1), ("a", 2)]

    def test_loc_missing_label_raises_keyerror(self, ns):
        obj = self._setup(ns)
        with pytest.raises(KeyError):
            obj.loc["zzz"]

    def test_loc_slice_rejected_points_to_value(self, ns):
        obj = self._setup(ns)
        with pytest.raises(TypeError) as exc:
            obj.loc["a":"b"]
        assert ".value" in str(exc.value)

    def test_loc_full_tuple_duplicate_returns_series(self, ns):
        idx = pd.MultiIndex.from_tuples([("a", 1), ("a", 1), ("b", 2)], names=["L0", "L1"])
        ns["dup"] = pd.Series([10, 20, 30], index=idx)
        result = ns["dup"].loc[("a", 1)]
        assert isinstance(result, pd.Series)
        assert list(result.values) == [10, 20]

    def test_loc_list_rejected_points_to_value(self, ns):
        obj = self._setup(ns)
        with pytest.raises(TypeError) as exc:
            obj.loc[["a", "b"]]
        assert ".value" in str(exc.value)


class TestExtend:
    def test_extend_with_series(self, ns):
        ns["s"] = pd.Series([1, 2], index=["a", "b"], name="n", dtype="int64")
        obj = ns.get_object("s")
        obj.extend(pd.Series([3, 4], index=["c", "d"], dtype="int64"))
        result = obj.value
        assert list(result.values) == [1, 2, 3, 4]
        assert list(result.index) == ["a", "b", "c", "d"]

    def test_extend_with_tuple_list(self, ns):
        ns["s"] = pd.Series([1], index=["a"], dtype="int64")
        obj = ns.get_object("s")
        obj.extend([("b", 2), ("c", 3)])
        result = obj.value
        assert list(result.values) == [1, 2, 3]
        assert list(result.index) == ["a", "b", "c"]

    def test_extend_with_list_of_series(self, ns):
        ns["s"] = pd.Series([1], index=["a"], dtype="int64")
        obj = ns.get_object("s")
        obj.extend([pd.Series([2], index=["b"], dtype="int64"), pd.Series([3], index=["c"], dtype="int64")])
        assert list(obj.value.index) == ["a", "b", "c"]

    def test_extend_creates_duplicate_labels(self, ns):
        ns["s"] = pd.Series([1], index=["a"], dtype="int64")
        obj = ns.get_object("s")
        obj.extend([("a", 2)])
        result = obj.value
        assert list(result.index) == ["a", "a"]
        assert list(result.values) == [1, 2]

    def test_extend_range_index_series_extends_range(self, ns):
        ns["s"] = pd.Series([1, 2, 3])  # RangeIndex 0..3
        obj = ns.get_object("s")
        obj.extend(pd.Series([4, 5]))  # also RangeIndex
        result = obj.value
        assert list(result.index) == [0, 1, 2, 3, 4]
        assert list(result.values) == [1, 2, 3, 4, 5]

    def test_extend_range_index_with_labeled_promotes(self, ns):
        ns["s"] = pd.Series([1, 2, 3])  # RangeIndex
        obj = ns.get_object("s")
        obj.extend([("x", 4)])
        result = obj.value
        assert list(result.index) == [0, 1, 2, "x"]
        assert list(result.values) == [1, 2, 3, 4]

    def test_extend_range_with_container_index_label_leaves_object_untouched(self, ns):
        from gpdatacached.extensions.pandas_support import UnsupportedElementTypeError

        ns["rs"] = pd.Series([10, 20])  # default RangeIndex
        obj = ns.get_object("rs")
        raw_before = ns._redis().hgetall(obj.raw_key)
        with pytest.raises(UnsupportedElementTypeError):
            obj.extend([([1, 2], 99)])
        raw_after = ns._redis().hgetall(obj.raw_key)
        # index representation must be unchanged (still range, not promoted to levels)
        assert raw_after.get("index_kind") == raw_before.get("index_kind")
        assert "range_start" in raw_after and "index_l0" not in raw_after
        # data unchanged
        assert raw_after.get("data") == raw_before.get("data")

    def test_extend_range_with_multilevel_input_raises_without_promote(self, ns):
        ns["rs"] = pd.Series([1, 2])  # range
        obj = ns.get_object("rs")
        raw_before = ns._redis().hgetall(obj.raw_key)
        with pytest.raises(ValueError, match="levels"):
            obj.extend(pd.Series([9], index=pd.MultiIndex.from_tuples([("a", 1)])))
        raw_after = ns._redis().hgetall(obj.raw_key)
        assert raw_after.get("index_kind") == raw_before.get("index_kind")  # still range, not promoted

    def test_extend_range_then_incremental_heterogeneous_labels(self, ns):
        ns["rs"] = pd.Series([1, 2, 3])  # range
        obj = ns.get_object("rs")
        obj.extend([("x", 4)])
        obj.extend([("y", 5)])  # must NOT fail — dtype honestly broadened to object on promote
        result = obj.value
        assert list(result.values) == [1, 2, 3, 4, 5]
        assert list(result.index) == [0, 1, 2, "x", "y"]

    def test_extend_incompatible_dtype_raises(self, ns):
        ns["s"] = pd.Series([1, 2], dtype="int64")
        obj = ns.get_object("s")
        with pytest.raises((ValueError, TypeError)):
            obj.extend([("a", "not_an_int")])

    def test_extend_returns_none(self, ns):
        ns["s"] = pd.Series([1], dtype="int64")
        obj = ns.get_object("s")
        assert obj.extend(pd.Series([2], dtype="int64")) is None

    def test_extend_categorical_raises(self, ns):
        ns["s"] = pd.Series(["a", "b"], dtype=pd.CategoricalDtype(["a", "b"]))
        obj = ns.get_object("s")
        with pytest.raises(TypeError, match="categor"):
            obj.extend([("x", "c")])


class TestSeriesMultiIndexExtend:
    def _make(self, ns):
        idx = pd.MultiIndex.from_tuples([("a", 1), ("a", 2)], names=["L0", "L1"])
        ns["ms"] = pd.Series([10, 20], index=idx)
        return ns.get_object("ms")

    def test_extend_with_series_same_multiindex(self, ns):
        obj = self._make(ns)
        obj.extend(pd.Series([30, 40], index=pd.MultiIndex.from_tuples([("b", 1), ("b", 2)])))
        assert list(obj.value.values) == [10, 20, 30, 40]
        assert list(obj.value.index) == [("a", 1), ("a", 2), ("b", 1), ("b", 2)]

    def test_extend_with_tuple_pairs(self, ns):
        obj = self._make(ns)
        obj.extend([(("b", 3), 99), (("c", 4), 88)])
        assert list(obj.value.values) == [10, 20, 99, 88]
        assert list(obj.value.index) == [("a", 1), ("a", 2), ("b", 3), ("c", 4)]

    def test_extend_level_count_mismatch_raises(self, ns):
        obj = self._make(ns)
        with pytest.raises(ValueError, match="levels"):
            obj.extend(pd.Series([1], index=["just_one"]))

    def test_extend_dtype_cast_failure_raises(self, ns):
        obj = self._make(ns)
        bad = pd.Series([1], index=pd.MultiIndex.from_tuples([("a", "not_int")]))
        with pytest.raises(ValueError):
            obj.extend(bad)

    def test_extend_list_of_series(self, ns):
        obj = self._make(ns)
        more1 = pd.Series([30], index=pd.MultiIndex.from_tuples([("b", 1)]))
        more2 = pd.Series([40], index=pd.MultiIndex.from_tuples([("b", 2)]))
        obj.extend([more1, more2])
        assert list(obj.value.values) == [10, 20, 30, 40]
        assert list(obj.value.index) == [("a", 1), ("a", 2), ("b", 1), ("b", 2)]


class TestRejectedOperations:
    def test_getitem_not_defined(self, ns):
        ns["s"] = pd.Series([1, 2, 3])
        obj = ns.get_object("s")
        with pytest.raises(TypeError):
            obj[0]

    def test_setitem_not_defined(self, ns):
        ns["s"] = pd.Series([1, 2, 3])
        obj = ns.get_object("s")
        with pytest.raises(TypeError):
            obj[0] = 99

    def test_delitem_not_defined(self, ns):
        ns["s"] = pd.Series([1, 2, 3])
        obj = ns.get_object("s")
        with pytest.raises(TypeError):
            del obj[0]

    def test_iloc_setter_rejected(self, ns):
        ns["s"] = pd.Series([1, 2, 3])
        obj = ns.get_object("s")
        with pytest.raises((TypeError, AttributeError)):
            obj.iloc[0] = 99


class TestNestingAndRefCounting:
    def test_series_as_list_element_round_trips(self, ns):
        ns["lst"] = [pd.Series([1, 2], dtype="int64"), pd.Series([3, 4], dtype="int64")]
        lst = ns.get_object("lst")
        # Nested access returns SeriesObject wrappers; go through .value for the plain Series.
        assert list(lst[0].value.values) == [1, 2]
        assert list(lst[1].value.values) == [3, 4]

    def test_series_as_dict_value_round_trips(self, ns):
        ns["d"] = {"a": pd.Series([1, 2], dtype="int64")}
        d = ns.get_object("d")
        assert list(d["a"].value.values) == [1, 2]

    def test_delete_named_series_releases_children(self, ns, raw_redis):
        from gpdatacached.keys import make_raw_key

        ns["s"] = pd.Series([1, 2, 3], index=["a", "b", "c"], dtype="int64")
        obj = ns.get_object("s")
        data_child = obj._get_data_list()
        index_child = obj._get_level_list(0)
        assert raw_redis.exists(data_child.raw_key)
        assert raw_redis.exists(index_child.raw_key)

        del ns["s"]

        assert not raw_redis.exists(data_child.raw_key)
        assert not raw_redis.exists(index_child.raw_key)
        assert not raw_redis.exists(make_raw_key("testdomain", "testns", "s"))

    def test_sliding_ttl_propagates_to_children(self, ns, raw_redis):
        ns["s"] = pd.Series([1, 2, 3], dtype="int64")
        obj = ns.get_object("s")
        obj.update_cache_mode_and_ttl("sliding", 300)
        data_child = obj._get_data_list()
        assert raw_redis.ttl(obj.raw_key) > 0
        assert raw_redis.ttl(data_child.raw_key) > 0


class TestPublicAPI:
    def test_module_exports(self):
        import gpdatacached.extensions.pandas_support as m

        for name in ("SeriesObject", "SeriesCodec", "UnsupportedSeriesDtypeError", "register"):
            assert hasattr(m, name), name

    def test_register_is_idempotent(self):
        from gpdatacached.extensions.pandas_support import register
        from gpdatacached.types import GPDCTypeRegistry

        register()
        register()
        assert GPDCTypeRegistry.lookup_by_gpdc_type("collection:pandas_series") is not None

    def test_full_workflow(self, ns):
        ns["temps"] = pd.Series([36.5, 37.0, 36.8], index=["mon", "tue", "wed"], name="temp")
        obj = ns.get_object("temps")
        assert obj.iloc[1] == 37.0
        obj.extend([("thu", 36.9), ("fri", 37.1)])
        result = obj.value
        assert list(result.index) == ["mon", "tue", "wed", "thu", "fri"]
        assert list(result.values) == [36.5, 37.0, 36.8, 36.9, 37.1]
        assert result.name == "temp"
        assert str(result.dtype) == "float64"
        sub = obj.loc["tue"]
        assert sub == 37.0


class TestScalarElementValidation:
    """Unit tests for the _assert_scalar_encodable helper."""

    def test_accepts_native_scalars_and_none(self):
        from gpdatacached.extensions.pandas_support import _assert_scalar_encodable

        _assert_scalar_encodable([1, 2.5, True, "x", None], "elem")  # no raise

    def test_accepts_na_sentinels(self):
        import numpy as np
        from gpdatacached.extensions.pandas_support import _assert_scalar_encodable

        # All standard NA markers must be allowed (consistent with _python_values,
        # which normalizes them to None). extend's dict/list path passes these raw.
        _assert_scalar_encodable([pd.NA, pd.NaT, np.nan, None], "elem")

    def test_accepts_tuple(self):
        from gpdatacached.extensions.pandas_support import _assert_scalar_encodable

        _assert_scalar_encodable([(1, 2), (3, 4)], "elem")  # tuple is a scalar codec

    def test_normalizes_numpy_scalars(self):
        import numpy as np
        from gpdatacached.extensions.pandas_support import _assert_scalar_encodable

        _assert_scalar_encodable([np.int64(1), np.bool_(True), np.float64(2.5)], "elem")

    def test_rejects_list_element(self):
        from gpdatacached.extensions.pandas_support import (
            UnsupportedElementTypeError,
            _assert_scalar_encodable,
        )

        with pytest.raises(UnsupportedElementTypeError):
            _assert_scalar_encodable([[1, 2]], "elem")

    def test_rejects_dict_and_set(self):
        from gpdatacached.extensions.pandas_support import (
            UnsupportedElementTypeError,
            _assert_scalar_encodable,
        )

        for bad in ([{"a": 1}], [{1, 2}]):
            with pytest.raises(UnsupportedElementTypeError):
                _assert_scalar_encodable(bad, "elem")

    def test_rejects_unregistered_type(self):
        from gpdatacached.extensions.pandas_support import (
            UnsupportedElementTypeError,
            _assert_scalar_encodable,
        )

        class Foo:
            pass

        with pytest.raises(UnsupportedElementTypeError):
            _assert_scalar_encodable([Foo()], "elem")


class TestCreateElementBoundary:
    def test_create_series_rejects_container_element(self, ns):
        from gpdatacached.extensions.pandas_support import (
            SeriesCodec,
            UnsupportedElementTypeError,
        )

        with pytest.raises(UnsupportedElementTypeError):
            SeriesCodec.create_raw(
                ns,
                "gpdc:testdomain:testns:raw:rej-s",
                pd.Series([[1, 2], [3, 4]], dtype=object),
                "owner",
                "permanent",
                0,
            )

    def test_create_series_rejects_dict_element(self, ns):
        from gpdatacached.extensions.pandas_support import (
            SeriesCodec,
            UnsupportedElementTypeError,
        )

        with pytest.raises(UnsupportedElementTypeError):
            SeriesCodec.create_raw(
                ns,
                "gpdc:testdomain:testns:raw:rej-d",
                pd.Series([{"a": 1}], dtype=object),
                "owner",
                "permanent",
                0,
            )

    def test_create_series_accepts_tuple_elements(self, ns):
        from gpdatacached.extensions.pandas_support import SeriesCodec

        raw_key = "gpdc:testdomain:testns:raw:ok-tuple"
        SeriesCodec.create_raw(ns, raw_key, pd.Series([(1, 2), (3, 4)], dtype=object), "owner", "permanent", 0)
        result = SeriesCodec.read_raw(ns, raw_key)
        assert list(result.values) == [(1, 2), (3, 4)]

    def test_create_series_accepts_numpy_scalars_in_object(self, ns):
        import numpy as np
        from gpdatacached.extensions.pandas_support import SeriesCodec

        raw_key = "gpdc:testdomain:testns:raw:ok-np"
        s = pd.Series([np.int64(1), np.bool_(True), np.float64(2.5)], dtype=object)
        SeriesCodec.create_raw(ns, raw_key, s, "owner", "permanent", 0)
        result = SeriesCodec.read_raw(ns, raw_key)
        assert list(result.values) == [1, True, 2.5]

    def test_create_series_rejects_container_index_label(self, ns):
        from gpdatacached.extensions.pandas_support import (
            SeriesCodec,
            UnsupportedElementTypeError,
        )

        with pytest.raises(UnsupportedElementTypeError):
            SeriesCodec.create_raw(
                ns,
                "gpdc:testdomain:testns:raw:rej-idx",
                pd.Series([1, 2], index=pd.Index([[1, 2], [3, 4]], dtype=object)),
                "owner",
                "permanent",
                0,
            )


class TestExtendElementBoundary:
    def test_series_extend_rejects_container_element(self, ns):
        from gpdatacached.extensions.pandas_support import UnsupportedElementTypeError

        ns["s"] = pd.Series(["a", "b"], dtype=object)
        obj = ns.get_object("s")
        with pytest.raises(UnsupportedElementTypeError):
            obj.extend([("idx0", [1, 2])])
        # object untouched: values unchanged AND index not promoted (stays RangeIndex)
        assert list(obj.value.values) == ["a", "b"]
        assert isinstance(obj.value.index, pd.RangeIndex)

    def test_series_extend_rejects_container_index_label(self, ns):
        from gpdatacached.extensions.pandas_support import UnsupportedElementTypeError

        ns["s"] = pd.Series(["a", "b"], index=["x", "y"], dtype=object)
        obj = ns.get_object("s")
        with pytest.raises(UnsupportedElementTypeError):
            obj.extend([([1, 2], "c")])
        assert list(obj.value.index) == ["x", "y"]


class TestPydanticElementBoundary:
    def test_scalar_registered_pydantic_model_accepted_as_element(self, ns):
        from pydantic import BaseModel

        from gpdatacached.extensions.pandas_support import SeriesCodec
        from gpdatacached.extensions.pydantic_support import PydanticModelScalarCodec
        from gpdatacached.scalar import GPDCScalarObject

        class Point(BaseModel):
            x: int
            y: int

        class PointObject(GPDCScalarObject):
            TYPE = "scalar:Point"

        GPDCTypeRegistry.register(
            python_type=Point,
            gpdc_type="scalar:Point",
            object_class=PointObject,
            codec_class=PydanticModelScalarCodec,
            priority=0,
        )

        raw_key = "gpdc:testdomain:testns:raw:point"
        s = pd.Series([Point(x=1, y=2), Point(x=3, y=4)], dtype=object)
        SeriesCodec.create_raw(ns, raw_key, s, "owner", "permanent", 0)
        result = SeriesCodec.read_raw(ns, raw_key)
        assert result.iloc[0] == Point(x=1, y=2)

    def test_container_registered_pydantic_model_rejected_as_element(self, ns):
        from pydantic import BaseModel

        from gpdatacached.extensions.pandas_support import (
            SeriesCodec,
            UnsupportedElementTypeError,
        )
        from gpdatacached.extensions.pydantic_support import (
            BaseModelContainerObject,
            PydanticModelContainerCodec,
        )

        class Order(BaseModel):
            order_id: str
            total: float

        class OrderObject(BaseModelContainerObject):
            TYPE = "collection:Order"

        GPDCTypeRegistry.register(
            python_type=Order,
            gpdc_type="collection:Order",
            object_class=OrderObject,
            codec_class=PydanticModelContainerCodec,
            priority=0,
        )

        with pytest.raises(UnsupportedElementTypeError):
            SeriesCodec.create_raw(
                ns,
                "gpdc:testdomain:testns:raw:order",
                pd.Series([Order(order_id="a", total=1.0)], dtype=object),
                "owner",
                "permanent",
                0,
            )


class TestStaleHandleLogic02Series:
    """CQR-2026-07-24 logic.02: a stale Series handle must raise before any Redis write."""

    def _delete_meta(self, obj, raw_redis):
        raw_redis.delete(obj._canonical_redis_key())

    def test_extend_raises_and_leaves_raw_untouched(self, ns, raw_redis):
        from gpdatacached.exceptions import ObjectGoneError

        ns["s"] = pd.Series([1, 2, 3], name="vals")
        obj = ns.get_object("s")
        before = raw_redis.hgetall(obj.raw_key)  # snapshot raw: before stale write
        self._delete_meta(obj, raw_redis)
        with pytest.raises(ObjectGoneError, match="no longer exists"):
            obj.extend(pd.Series([4, 5]))
        # raw: contents must be unchanged (the stale write must not have appended).
        assert raw_redis.hgetall(obj.raw_key) == before
