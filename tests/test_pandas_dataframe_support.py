from __future__ import annotations

import threading

import pandas as pd
import pytest

from gpdatacached.types import GPDCTypeRegistry


@pytest.fixture(autouse=True)
def _register_pandas():
    """Isolate registry and register both Series + DataFrame for every test."""
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


class TestValidateDataFrame:
    def test_valid_dataframe_returns_columns_and_kind(self):
        from gpdatacached.extensions.pandas_support import _validate_dataframe

        df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
        columns, kind = _validate_dataframe(df)
        assert list(columns) == ["a", "b"]
        assert kind == "range"

    def test_duplicate_columns_rejected(self):
        from gpdatacached.extensions.pandas_support import (
            UnsupportedDataFrameError,
            _validate_dataframe,
        )

        df = pd.DataFrame([[1, 2]])
        df.columns = ["a", "a"]
        with pytest.raises(UnsupportedDataFrameError, match="Duplicate column names"):
            _validate_dataframe(df)

    def test_multiindex_rejected(self):
        from gpdatacached.extensions.pandas_support import (
            UnsupportedDataFrameError,
            _validate_dataframe,
        )

        df = pd.DataFrame(
            {"a": [1, 2]},
            index=pd.MultiIndex.from_tuples([("x", 1), ("x", 2)]),
        )
        with pytest.raises(UnsupportedDataFrameError, match="MultiIndex"):
            _validate_dataframe(df)

    def test_unsupported_column_dtype_rejected(self):
        from gpdatacached.extensions.pandas_support import (
            UnsupportedDataFrameError,
            _validate_dataframe,
        )

        df = pd.DataFrame({"a": pd.Series([1, 2], dtype="timedelta64[ns]")})
        with pytest.raises(UnsupportedDataFrameError) as exc_info:
            _validate_dataframe(df)
        assert "'a'" in str(exc_info.value)
        assert "timedelta" in str(exc_info.value)

    def test_error_message_guides_to_custom_codec(self):
        from gpdatacached.extensions.pandas_support import UnsupportedDataFrameError

        with pytest.raises(UnsupportedDataFrameError) as exc_info:
            raise UnsupportedDataFrameError("boom")
        assert "GPDCTypeRegistry.register" in str(exc_info.value)


class TestCreateRawHashLayout:
    def test_range_index_df_writes_column_refs_and_range_fields(self, ns):
        from gpdatacached.extensions.pandas_support import DataFrameCodec
        from gpdatacached.types import encode_key

        df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
        raw_key = "gpdc:testdomain:testns:raw:df1"
        try:
            DataFrameCodec.create_raw(ns, raw_key, df, "owner", "permanent", 0)
            fields = ns._redis().hgetall(raw_key)
            assert fields[encode_key("a")].startswith("ref:")
            assert fields[encode_key("b")].startswith("ref:")
            assert fields[encode_key("a")] != fields[encode_key("b")]
            assert fields["~dt:" + encode_key("a")] == "str:int64"
            assert fields["~index_kind"] == "str:range"
            assert fields["~range_start"] == "int:0"
            assert fields["~range_stop"] == "int:3"
            assert "~index" not in fields  # RangeIndex omits the index child
        finally:
            ns._redis().delete(raw_key)

    def test_labeled_index_df_writes_shared_index_ref(self, ns):
        from gpdatacached.extensions.pandas_support import DataFrameCodec

        df = pd.DataFrame({"a": [1, 2]}, index=["r1", "r2"])
        raw_key = "gpdc:testdomain:testns:raw:df2"
        try:
            DataFrameCodec.create_raw(ns, raw_key, df, "owner", "permanent", 0)
            fields = ns._redis().hgetall(raw_key)
            assert fields["~index"].startswith("ref:")
            assert fields["~index_kind"] == "str:labels"
            assert fields["~index_dtype"] == "str:str"
        finally:
            ns._redis().delete(raw_key)

    def test_category_column_writes_cats_and_ord_fields(self, ns):
        from gpdatacached.extensions.pandas_support import DataFrameCodec
        from gpdatacached.types import encode_key

        df = pd.DataFrame({"c": pd.Series(["a", "b"], dtype=pd.CategoricalDtype(["a", "b"], ordered=True))})
        raw_key = "gpdc:testdomain:testns:raw:df3"
        try:
            DataFrameCodec.create_raw(ns, raw_key, df, "owner", "permanent", 0)
            fields = ns._redis().hgetall(raw_key)
            ck = encode_key("c")
            assert fields["~cat:" + ck].startswith("ref:")
            assert fields["~ord:" + ck] == "bool:True"
        finally:
            ns._redis().delete(raw_key)

    def test_columns_tuple_preserves_order(self, ns):
        from gpdatacached.extensions.pandas_support import DataFrameCodec
        from gpdatacached.types import _decode_scalar

        df = pd.DataFrame({"z": [1], "a": [2], "m": [3]})
        raw_key = "gpdc:testdomain:testns:raw:df4"
        try:
            DataFrameCodec.create_raw(ns, raw_key, df, "owner", "permanent", 0)
            fields = ns._redis().hgetall(raw_key)
            assert _decode_scalar(fields["~columns"]) == ("z", "a", "m")
        finally:
            ns._redis().delete(raw_key)

    def test_create_raw_validates_dtypes(self, ns):
        from gpdatacached.extensions.pandas_support import (
            DataFrameCodec,
            UnsupportedDataFrameError,
        )

        df = pd.DataFrame({"a": pd.Series([1, 2], dtype="timedelta64[ns]")})
        raw_key = "gpdc:testdomain:testns:raw:df5"
        with pytest.raises(UnsupportedDataFrameError):
            DataFrameCodec.create_raw(ns, raw_key, df, "owner", "permanent", 0)
