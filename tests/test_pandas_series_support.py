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


class TestSeriesObjectCore:
    def test_store_and_get_value(self, ns):
        series = pd.Series([1, 2, 3], index=["a", "b", "c"], name="n")
        ns["s"] = series
        obj = ns.get_object("s")
        assert obj.type == "collection:pandas_series"
        assert list(obj.value.values) == [1, 2, 3]
        assert list(obj.value.index) == ["a", "b", "c"]

    def test_len_dtype_name_index_name(self, ns):
        ns["s"] = pd.Series([1, 2, 3], index=["a", "b", "c"], name="vals")
        obj = ns.get_object("s")
        assert len(obj) == 3
        assert obj.dtype == "int64"
        assert obj.name == "vals"
        assert obj.index_name is None

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
        assert list(result.values) == [20]
        assert list(result.index) == ["b"]

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
        index_child = obj._get_index_list()
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
        assert list(sub.values) == [37.0]


class TestScalarElementValidation:
    """Unit tests for the _assert_scalar_encodable helper."""

    def test_accepts_native_scalars_and_none(self):
        from gpdatacached.extensions.pandas_support import _assert_scalar_encodable

        _assert_scalar_encodable([1, 2.5, True, "x", None], "elem")  # no raise

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
