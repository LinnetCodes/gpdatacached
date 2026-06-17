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
            assert ns._redis().hget(raw_key, "data").startswith("ref:")
            assert ns._redis().hget(raw_key, "index").startswith("ref:")
            assert ns._redis().hget(raw_key, "dtype") == "str:int64"
        finally:
            ns._redis().delete(raw_key)
