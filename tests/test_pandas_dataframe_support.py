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


class TestDataFrameRoundTrip:
    @pytest.mark.parametrize(
        "df",
        [
            pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]}),  # int columns, RangeIndex
            pd.DataFrame({"a": [1.5, 2.5], "b": ["x", "y"]}, index=["r1", "r2"]),  # labeled
            pd.DataFrame({"a": [True, False], "b": [1, 2]}),  # bool + int
            pd.DataFrame({"s": pd.Series([1, None, 3], dtype="Int64")}),  # nullable + NA
            pd.DataFrame({"d": pd.to_datetime(["2020-01-01", "2021-01-01"])}),  # datetime naive
            pd.DataFrame({"d": pd.to_datetime(["2020-01-01", "2021-01-01"], utc=True)}),  # datetime tz
            pd.DataFrame({"c": pd.Series(["a", "b", "a"], dtype="category")}),  # category
            pd.DataFrame({"o": [{"k": 1}, [1, 2], "s"]}),  # object with nested containers
        ],
    )
    def test_round_trip_preserves_all(self, ns, df):
        from gpdatacached.extensions.pandas_support import DataFrameCodec

        raw_key = f"gpdc:testdomain:testns:raw:rt-{id(df)}"
        try:
            DataFrameCodec.create_raw(ns, raw_key, df, "owner", "permanent", 0)
            result = DataFrameCodec.read_raw(ns, raw_key)
        finally:
            ns._redis().delete(raw_key)

        assert list(result.columns) == list(df.columns)
        assert list(result.index) == list(df.index)
        assert list(result.dtypes.astype(str)) == list(df.dtypes.astype(str))
        for col in df.columns:
            assert list(result[col].tolist()) == list(df[col].tolist())

    def test_round_trip_preserves_category_categories_and_ordered(self, ns):
        from gpdatacached.extensions.pandas_support import DataFrameCodec

        df = pd.DataFrame({"c": pd.Series(["b", "a"], dtype=pd.CategoricalDtype(["a", "b", "c"], ordered=True))})
        raw_key = "gpdc:testdomain:testns:raw:catrt"
        try:
            DataFrameCodec.create_raw(ns, raw_key, df, "owner", "permanent", 0)
            result = DataFrameCodec.read_raw(ns, raw_key)
        finally:
            ns._redis().delete(raw_key)
        assert list(result["c"].cat.categories) == ["a", "b", "c"]
        assert result["c"].cat.ordered is True

    def test_round_trip_empty_dataframe(self, ns):
        from gpdatacached.extensions.pandas_support import DataFrameCodec

        df = pd.DataFrame({"a": [], "b": []})
        raw_key = "gpdc:testdomain:testns:raw:empty"
        try:
            DataFrameCodec.create_raw(ns, raw_key, df, "owner", "permanent", 0)
            result = DataFrameCodec.read_raw(ns, raw_key)
        finally:
            ns._redis().delete(raw_key)
        assert list(result.columns) == ["a", "b"]
        assert len(result) == 0

    def test_tuple_column_names_round_trip(self, ns):
        from gpdatacached.extensions.pandas_support import DataFrameCodec

        df = pd.DataFrame({("g", "a"): [1, 2], ("g", "b"): [3, 4]})
        raw_key = "gpdc:testdomain:testns:raw:tuplecols"
        try:
            DataFrameCodec.create_raw(ns, raw_key, df, "owner", "permanent", 0)
            result = DataFrameCodec.read_raw(ns, raw_key)
        finally:
            ns._redis().delete(raw_key)
        assert list(result.columns) == [("g", "a"), ("g", "b")]


class TestDataFrameObjectCore:
    def test_store_and_get_value(self, ns):
        df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]}, index=["r1", "r2", "r3"])
        ns["df"] = df
        obj = ns.get_object("df")
        assert obj.type == "collection:pandas_dataframe"
        assert list(obj.value.columns) == ["a", "b"]
        assert list(obj.value.index) == ["r1", "r2", "r3"]
        assert list(obj.value["a"]) == [1, 2, 3]

    def test_value_setter_rejected(self, ns):
        ns["df"] = pd.DataFrame({"a": [1]})
        obj = ns.get_object("df")
        with pytest.raises(TypeError):
            obj.value = pd.DataFrame({"a": [9]})

    def test_register_registers_both_series_and_dataframe(self):
        from gpdatacached.extensions.pandas_support import register
        from gpdatacached.types import GPDCTypeRegistry

        register()  # idempotent; fixture already registered, confirm both stay present
        assert GPDCTypeRegistry.lookup_by_gpdc_type("collection:pandas_series") is not None
        assert GPDCTypeRegistry.lookup_by_gpdc_type("collection:pandas_dataframe") is not None

    def test_module_exports(self):
        import gpdatacached.extensions.pandas_support as m

        for name in ("DataFrameObject", "DataFrameCodec", "UnsupportedDataFrameError", "register"):
            assert hasattr(m, name), name


class TestReadApi:
    def test_len_returns_row_count_range(self, ns):
        ns["df"] = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
        obj = ns.get_object("df")
        assert len(obj) == 3

    def test_len_returns_row_count_labels(self, ns):
        ns["df"] = pd.DataFrame({"a": [1, 2]}, index=["x", "y"])
        obj = ns.get_object("df")
        assert len(obj) == 2

    def test_columns_returns_tuple(self, ns):
        ns["df"] = pd.DataFrame({"z": [1], "a": [2]})
        obj = ns.get_object("df")
        assert obj.columns == ("z", "a")

    def test_index_name(self, ns):
        ns["df"] = pd.DataFrame({"a": [1]}, index=pd.Index(["x"], name="rows"))
        obj = ns.get_object("df")
        assert obj.index_name == "rows"

    def test_contains_column(self, ns):
        ns["df"] = pd.DataFrame({"a": [1], "b": [2]})
        obj = ns.get_object("df")
        assert "a" in obj
        assert "c" not in obj

    def test_contains_unsupported_type_returns_false(self, ns):
        ns["df"] = pd.DataFrame({"a": [1]})
        obj = ns.get_object("df")
        # encode_key raises TypeError for container types; __contains__ must catch it
        assert ([1, 2] in obj) is False
        assert ({"k": 1} in obj) is False

    def test_iter_yields_column_names(self, ns):
        ns["df"] = pd.DataFrame({"a": [1], "b": [2]})
        obj = ns.get_object("df")
        assert list(iter(obj)) == ["a", "b"]

    def test_getitem_returns_column_series(self, ns):
        ns["df"] = pd.DataFrame({"a": [1, 2, 3]}, index=["r1", "r2", "r3"])
        obj = ns.get_object("df")
        col = obj["a"]
        assert isinstance(col, pd.Series)
        assert list(col.values) == [1, 2, 3]
        assert list(col.index) == ["r1", "r2", "r3"]
        assert str(col.dtype) == "int64"

    def test_getitem_category_column(self, ns):
        ns["df"] = pd.DataFrame({"c": pd.Series(["a", "b"], dtype=pd.CategoricalDtype(["a", "b"], ordered=True))})
        obj = ns.get_object("df")
        col = obj["c"]
        assert list(col.cat.categories) == ["a", "b"]
        assert col.cat.ordered is True

    def test_getitem_unknown_column_raises(self, ns):
        ns["df"] = pd.DataFrame({"a": [1]})
        obj = ns.get_object("df")
        with pytest.raises(KeyError):
            obj["zzz"]


class TestDfILoc:
    def test_iloc_int_returns_row_series(self, ns):
        ns["df"] = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]}, index=["r1", "r2", "r3"])
        obj = ns.get_object("df")
        row = obj.iloc[1]
        assert isinstance(row, pd.Series)
        assert list(row.index) == ["a", "b"]
        assert row["a"] == 2
        assert row["b"] == "y"
        assert row.name == "r2"

    def test_iloc_int_negative(self, ns):
        ns["df"] = pd.DataFrame({"a": [1, 2, 3]})
        obj = ns.get_object("df")
        assert obj.iloc[-1]["a"] == 3

    def test_iloc_int_numpy_integer(self, ns):
        import numpy as np

        ns["df"] = pd.DataFrame({"a": [10, 20]})
        obj = ns.get_object("df")
        assert obj.iloc[np.int64(1)]["a"] == 20

    def test_iloc_slice_returns_dataframe(self, ns):
        ns["df"] = pd.DataFrame({"a": [1, 2, 3, 4], "b": [5, 6, 7, 8]}, index=["r1", "r2", "r3", "r4"])
        obj = ns.get_object("df")
        sub = obj.iloc[1:3]
        assert isinstance(sub, pd.DataFrame)
        assert list(sub.index) == ["r2", "r3"]
        assert list(sub["a"]) == [2, 3]
        assert list(sub["b"]) == [6, 7]

    def test_iloc_slice_on_range_index(self, ns):
        ns["df"] = pd.DataFrame({"a": [10, 20, 30]})
        obj = ns.get_object("df")
        sub = obj.iloc[0:2]
        assert list(sub.index) == [0, 1]
        assert list(sub["a"]) == [10, 20]

    def test_iloc_bad_key_type_raises(self, ns):
        ns["df"] = pd.DataFrame({"a": [1]})
        obj = ns.get_object("df")
        with pytest.raises(TypeError):
            obj.iloc["a"]


class TestDfLoc:
    def test_loc_single_match_returns_series(self, ns):
        ns["df"] = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]}, index=["r1", "r2", "r3"])
        obj = ns.get_object("df")
        row = obj.loc["r2"]
        assert isinstance(row, pd.Series)
        assert row["a"] == 2
        assert row.name == "r2"

    def test_loc_duplicate_labels_returns_dataframe(self, ns):
        ns["df"] = pd.DataFrame({"a": [1, 2, 3]}, index=["r1", "r1", "r3"])
        obj = ns.get_object("df")
        result = obj.loc["r1"]
        assert isinstance(result, pd.DataFrame)
        assert list(result.index) == ["r1", "r1"]
        assert list(result["a"]) == [1, 2]

    def test_loc_missing_label_raises(self, ns):
        ns["df"] = pd.DataFrame({"a": [1]}, index=["r1"])
        obj = ns.get_object("df")
        with pytest.raises(KeyError):
            obj.loc["zzz"]

    def test_loc_on_range_index_raises(self, ns):
        ns["df"] = pd.DataFrame({"a": [1, 2]})
        obj = ns.get_object("df")
        with pytest.raises(TypeError):
            obj.loc[0]


class TestDfExtend:
    def test_extend_with_dataframe(self, ns):
        ns["df"] = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
        obj = ns.get_object("df")
        obj.extend(pd.DataFrame({"a": [5, 6], "b": [7, 8]}))
        result = obj.value
        assert list(result.index) == [0, 1, 2, 3]
        assert list(result["a"]) == [1, 2, 5, 6]
        assert list(result["b"]) == [3, 4, 7, 8]

    def test_extend_with_dict_of_lists(self, ns):
        ns["df"] = pd.DataFrame({"a": [1], "b": [2]})
        obj = ns.get_object("df")
        obj.extend({"a": [3, 4], "b": [5, 6]})
        assert list(obj.value["a"]) == [1, 3, 4]
        assert list(obj.value["b"]) == [2, 5, 6]

    def test_extend_with_list_of_dicts(self, ns):
        ns["df"] = pd.DataFrame({"a": [1], "b": [2]})
        obj = ns.get_object("df")
        obj.extend([{"a": 3, "b": 4}, {"a": 5, "b": 6}])
        assert list(obj.value["a"]) == [1, 3, 5]

    def test_extend_column_set_mismatch_raises(self, ns):
        ns["df"] = pd.DataFrame({"a": [1], "b": [2]})
        obj = ns.get_object("df")
        with pytest.raises(ValueError):
            obj.extend(pd.DataFrame({"a": [3]}))

    def test_extend_range_index_with_range_df_extends_range(self, ns):
        ns["df"] = pd.DataFrame({"a": [1, 2, 3]})
        obj = ns.get_object("df")
        obj.extend(pd.DataFrame({"a": [4, 5]}))
        result = obj.value
        assert list(result.index) == [0, 1, 2, 3, 4]
        assert ns.get_object("df")._index_kind() == "range"

    def test_extend_range_index_with_labeled_promotes(self, ns):
        ns["df"] = pd.DataFrame({"a": [1, 2, 3]})
        obj = ns.get_object("df")
        obj.extend(pd.DataFrame({"a": [4]}, index=["x"]))
        result = obj.value
        assert list(result.index) == [0, 1, 2, "x"]
        assert ns.get_object("df")._index_kind() == "labels"

    def test_extend_category_column_valid_values(self, ns):
        ns["df"] = pd.DataFrame({"c": pd.Series(["a", "b"], dtype=pd.CategoricalDtype(["a", "b"]))})
        obj = ns.get_object("df")
        obj.extend({"c": ["a", "b"]})
        assert list(obj.value["c"]) == ["a", "b", "a", "b"]

    def test_extend_category_column_out_of_category_raises(self, ns):
        ns["df"] = pd.DataFrame({"c": pd.Series(["a", "b"], dtype=pd.CategoricalDtype(["a", "b"]))})
        obj = ns.get_object("df")
        with pytest.raises(ValueError, match="categor"):
            obj.extend({"c": ["z"]})

    def test_extend_incompatible_dtype_raises(self, ns):
        ns["df"] = pd.DataFrame({"a": [1, 2]})
        obj = ns.get_object("df")
        with pytest.raises(ValueError):
            obj.extend({"a": ["not_an_int"]})

    def test_extend_returns_none(self, ns):
        ns["df"] = pd.DataFrame({"a": [1]})
        obj = ns.get_object("df")
        assert obj.extend(pd.DataFrame({"a": [2]})) is None

    def test_extend_lockstep_all_columns_and_index_aligned(self, ns):
        ns["df"] = pd.DataFrame({"a": [1], "b": [2]}, index=["r1"])
        obj = ns.get_object("df")
        obj.extend(pd.DataFrame({"a": [3, 4], "b": [5, 6]}, index=["r2", "r3"]))
        result = obj.value
        assert len(result) == 3
        assert len(result.index) == len(result["a"]) == len(result["b"]) == 3

    def test_deepcopy_returns_plain_dataframe(self, ns):
        ns["df"] = pd.DataFrame({"a": [1, 2]})
        obj = ns.get_object("df")
        cp = obj.deepcopy()
        assert isinstance(cp, pd.DataFrame)
        assert list(cp["a"]) == [1, 2]

    def test_extend_ragged_dict_raises(self, ns):
        ns["df"] = pd.DataFrame({"a": [1], "b": [2]})
        obj = ns.get_object("df")
        with pytest.raises(ValueError, match="mismatched lengths"):
            obj.extend({"a": [3, 4], "b": [5]})

    def test_extend_category_column_with_na_via_dict(self, ns):
        # pd.NA in a category column via dict input must extend (NA allowed),
        # not crash on the membership guard (regression for the pd.NA bool-ambiguity bug:
        # the old `v is not None and ...` guard evaluated `pd.NA == cat` -> pd.NA, and
        # bool(pd.NA) raised TypeError). Uses pd.NA (not None) because None short-circuits
        # the old guard via `v is not None` and would NOT reproduce the crash.
        ns["df"] = pd.DataFrame({"c": pd.Series(["a", "b"], dtype=pd.CategoricalDtype(["a", "b"]))})
        obj = ns.get_object("df")
        obj.extend({"c": ["a", pd.NA]})  # pd.NA is NA; must be accepted, not crash
        result = obj.value
        assert list(result["c"][:2]) == ["a", "b"]
        assert list(result["c"][2:3]) == ["a"]
        assert pd.isna(result["c"].iloc[3])
