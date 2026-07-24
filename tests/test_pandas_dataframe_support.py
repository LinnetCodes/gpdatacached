from __future__ import annotations

import threading

import pandas as pd
import pytest

from gpdatacached.types import GPDCTypeRegistry, encode_key


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

    def test_multiindex_accepted_returns_levels(self):
        from gpdatacached.extensions.pandas_support import _validate_dataframe

        df = pd.DataFrame(
            {"a": [1, 2]},
            index=pd.MultiIndex.from_tuples([("x", 1), ("x", 2)]),
        )
        columns, kind = _validate_dataframe(df)
        assert list(columns) == ["a"]
        assert kind == "levels"

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

    def test_labeled_index_df_writes_level0_ref(self, ns):
        from gpdatacached.extensions.pandas_support import DataFrameCodec
        from gpdatacached.types import _decode_scalar

        df = pd.DataFrame({"a": [1, 2]}, index=["r1", "r2"])
        raw_key = "gpdc:testdomain:testns:raw:df2"
        try:
            DataFrameCodec.create_raw(ns, raw_key, df, "owner", "permanent", 0)
            fields = ns._redis().hgetall(raw_key)
            # single-level labelled now stores as L=1 "levels" (no legacy ~index)
            assert "~index" not in fields
            assert fields["~index_l0"].startswith("ref:")
            assert _decode_scalar(fields["~index_kind"]) == "levels"
            assert _decode_scalar(fields["~index_dtypes"]) == (str(df.index.dtype),)
            assert _decode_scalar(fields["~index_names"]) == (None,)
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


class TestDataFrameRangeIndexNonUnitStepLogic03:
    """CQR-2026-07-24 logic.03: non-unit RangeIndex step honoured by __len__,
    extend, and column assignment (previously __len__ used stop-start and extend
    added n instead of n*step)."""

    def test_len_non_unit_step(self, ns):
        ns["df"] = pd.DataFrame({"v": [10, 20, 30]}, index=pd.RangeIndex(5, 20, 5))
        obj = ns.get_object("df")
        assert len(obj) == 3  # not 15

    def test_len_negative_step(self, ns):
        ns["df"] = pd.DataFrame({"v": [30, 20, 10]}, index=pd.RangeIndex(20, 5, -5))
        obj = ns.get_object("df")
        assert len(obj) == 3

    def test_column_assignment_correct_length_accepted(self, ns):
        # Before the fix, __len__ returned 15 so a 3-value column was rejected.
        ns["df"] = pd.DataFrame({"v": [10, 20, 30]}, index=pd.RangeIndex(5, 20, 5))
        obj = ns.get_object("df")
        obj["w"] = [100, 200, 300]
        assert list(obj["w"]) == [100, 200, 300]

    def test_extend_non_unit_step_stays_compact_and_correct(self, ns):
        # Direction A: a non-unit RangeIndex stays compact (does NOT promote to levels)
        # and advances range_stop by n*step.
        ns["df"] = pd.DataFrame({"v": [1, 2, 3]}, index=pd.RangeIndex(5, 20, 5))
        obj = ns.get_object("df")
        obj.extend(pd.DataFrame({"v": [4, 5]}, index=pd.RangeIndex(20, 30, 5)))
        # Still compact range storage (logic.03 direction A):
        from gpdatacached.extensions.pandas_support import _decode_scalar

        assert _decode_scalar(obj._raw_fields()["~index_kind"]) == "range"
        val = obj.value
        assert len(val) == 5
        assert list(val.index) == [5, 10, 15, 20, 25]
        assert list(val["v"]) == [1, 2, 3, 4, 5]

    def test_extend_negative_step_stays_compact_and_correct(self, ns):
        ns["df"] = pd.DataFrame({"v": [3, 2, 1]}, index=pd.RangeIndex(20, 5, -5))
        obj = ns.get_object("df")
        obj.extend(pd.DataFrame({"v": [5, 0]}, index=pd.RangeIndex(5, -5, -5)))
        from gpdatacached.extensions.pandas_support import _decode_scalar

        assert _decode_scalar(obj._raw_fields()["~index_kind"]) == "range"
        val = obj.value
        assert len(val) == 5
        assert list(val.index) == [20, 15, 10, 5, 0]

    def test_extend_repeated_non_unit_step(self, ns):
        ns["df"] = pd.DataFrame({"v": [1, 2, 3]}, index=pd.RangeIndex(5, 20, 5))
        obj = ns.get_object("df")
        obj.extend(pd.DataFrame({"v": [4]}, index=pd.RangeIndex(20, 25, 5)))
        obj.extend(pd.DataFrame({"v": [5, 6]}, index=pd.RangeIndex(25, 35, 5)))
        val = obj.value
        assert list(val.index) == [5, 10, 15, 20, 25, 30]
        assert list(val["v"]) == [1, 2, 3, 4, 5, 6]

    def test_iloc_slice_non_unit_step(self, ns):
        ns["df"] = pd.DataFrame({"v": [10, 20, 30]}, index=pd.RangeIndex(5, 20, 5))
        obj = ns.get_object("df")
        sub = obj.iloc[0:2]
        assert list(sub.index) == [5, 10]


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


class TestDataFrameMultiIndexRowStorage:
    def test_two_level_row_index_round_trip(self, ns):
        from gpdatacached.extensions.pandas_support import DataFrameCodec

        idx = pd.MultiIndex.from_arrays([["a", "a", "b"], [1, 2, 1]], names=["L0", "L1"])
        original = pd.DataFrame({"x": [1, 2, 3], "y": [4.0, 5.0, 6.0]}, index=idx)
        raw_key = "gpdc:testdomain:testns:raw:dfmi"
        try:
            DataFrameCodec.create_raw(ns, raw_key, original, "owner", "permanent", 0)
            result = DataFrameCodec.read_raw(ns, raw_key)
        finally:
            ns._redis().delete(raw_key)
        assert isinstance(result.index, pd.MultiIndex)
        assert list(result.index) == list(original.index)
        # Per-level dtypes guard against the _build_levels_index except-fallback
        # silently masking a dtype mismatch (e.g. a regression that stores the
        # wrong dtype string).
        assert str(result.index.get_level_values(0).dtype) == str(original.index.get_level_values(0).dtype)
        assert str(result.index.get_level_values(1).dtype) == str(original.index.get_level_values(1).dtype)
        assert list(result.columns) == ["x", "y"]
        assert list(result["x"]) == [1, 2, 3]
        assert list(result["y"]) == [4.0, 5.0, 6.0]

    def test_raw_hash_has_tilde_prefixed_level_fields(self, ns):
        from gpdatacached.extensions.pandas_support import DataFrameCodec

        idx = pd.MultiIndex.from_arrays([["a", "b"], [1, 2]], names=["L0", "L1"])
        original = pd.DataFrame({"x": [1, 2]}, index=idx)
        raw_key = "gpdc:testdomain:testns:raw:dfmihash"
        try:
            DataFrameCodec.create_raw(ns, raw_key, original, "owner", "permanent", 0)
            assert ns._redis().hget(raw_key, "~index_kind") == "str:levels"
            assert ns._redis().hget(raw_key, "~index_l0").startswith("ref:")
            assert ns._redis().hget(raw_key, "~index_l1").startswith("ref:")
            assert ns._redis().hget(raw_key, "~index_l0") != ns._redis().hget(raw_key, "~index_l1")
            assert ns._redis().hget(raw_key, "~index_names").startswith("tuple:")
            assert ns._redis().hget(raw_key, "~index_dtypes").startswith("tuple:")
        finally:
            ns._redis().delete(raw_key)

    def test_l1_levels_row_index_equivalent_to_legacy_labels(self, ns):
        from gpdatacached.extensions.pandas_support import DataFrameCodec

        original = pd.DataFrame({"x": [1, 2, 3]}, index=["p", "q", "r"])
        raw_key = "gpdc:testdomain:testns:raw:dfl1"
        try:
            DataFrameCodec.create_raw(ns, raw_key, original, "owner", "permanent", 0)
            result = DataFrameCodec.read_raw(ns, raw_key)
        finally:
            ns._redis().delete(raw_key)
        assert list(result.index) == ["p", "q", "r"]
        assert list(result.columns) == ["x"]
        assert list(result["x"]) == [1, 2, 3]


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

    def test_index_names(self, ns):
        ns["df"] = pd.DataFrame({"a": [1]}, index=pd.Index(["x"], name="rows"))
        obj = ns.get_object("df")
        assert obj.index_names == ("rows",)

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

    def test_single_level_list_selection_returns_dataframe(self, ns):
        ns["sl"] = pd.DataFrame({"x": [1, 2], "y": [3, 4], "z": [5, 6]})
        sub = ns.get_object("sl")[["x", "z"]]
        assert isinstance(sub, pd.DataFrame)
        assert list(sub.columns) == ["x", "z"]
        assert list(sub["x"]) == [1, 2]


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
        assert ns.get_object("df")._index_kind() == "levels"

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


class TestColumnMutation:
    def test_setitem_new_column(self, ns):
        ns["df"] = pd.DataFrame({"a": [1, 2, 3]})
        obj = ns.get_object("df")
        obj["b"] = [4, 5, 6]
        result = obj.value
        assert list(result.columns) == ["a", "b"]
        assert list(result["b"]) == [4, 5, 6]

    def test_setitem_replace_column_same_dtype(self, ns):
        ns["df"] = pd.DataFrame({"a": [1, 2]})
        obj = ns.get_object("df")
        obj["a"] = [9, 8]
        assert list(obj.value["a"]) == [9, 8]

    def test_setitem_replace_column_dtype_change_cleans_stale_cat_fields(self, ns):
        ns["df"] = pd.DataFrame({"c": pd.Series(["a", "b"], dtype=pd.CategoricalDtype(["a", "b"], ordered=True))})
        obj = ns.get_object("df")
        from gpdatacached.types import encode_key

        ck = encode_key("c")
        assert ns._redis().hexists(obj.raw_key, "~cat:" + ck)
        obj["c"] = [1, 2]  # category -> int64
        assert not ns._redis().hexists(obj.raw_key, "~cat:" + ck)
        assert not ns._redis().hexists(obj.raw_key, "~ord:" + ck)
        assert str(obj.value["c"].dtype) == "int64"

    def test_setitem_wrong_length_raises(self, ns):
        ns["df"] = pd.DataFrame({"a": [1, 2, 3]})
        obj = ns.get_object("df")
        with pytest.raises(ValueError, match="length"):
            obj["b"] = [1, 2]

    def test_setitem_unsupported_dtype_raises(self, ns):
        ns["df"] = pd.DataFrame({"a": [1, 2]})
        obj = ns.get_object("df")
        from gpdatacached.extensions.pandas_support import UnsupportedDataFrameError

        with pytest.raises(UnsupportedDataFrameError):
            obj["b"] = pd.Series([1, 2], dtype="timedelta64[ns]")

    def test_setitem_bootstrap_empty_df_rejected(self, ns):
        ns["df"] = pd.DataFrame({"a": []})
        obj = ns.get_object("df")
        with pytest.raises(ValueError, match="empty"):
            obj["b"] = [1, 2, 3]

    def test_setitem_category_column(self, ns):
        ns["df"] = pd.DataFrame({"a": [1, 2]})
        obj = ns.get_object("df")
        obj["c"] = pd.Series(["x", "y"], dtype=pd.CategoricalDtype(["x", "y"], ordered=True))
        col = obj.value["c"]
        assert list(col.cat.categories) == ["x", "y"]
        assert col.cat.ordered is True

    def test_delitem_drops_column(self, ns):
        ns["df"] = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
        obj = ns.get_object("df")
        del obj["a"]
        result = obj.value
        assert list(result.columns) == ["b"]
        assert list(result["b"]) == [3, 4]

    def test_delitem_missing_raises_keyerror(self, ns):
        ns["df"] = pd.DataFrame({"a": [1]})
        obj = ns.get_object("df")
        with pytest.raises(KeyError):
            del obj["zzz"]

    def test_delitem_last_column_retains_index(self, ns):
        ns["df"] = pd.DataFrame({"a": [1, 2, 3]}, index=["r1", "r2", "r3"])
        obj = ns.get_object("df")
        del obj["a"]
        result = obj.value
        assert list(result.columns) == []
        assert list(result.index) == ["r1", "r2", "r3"]

    def test_delitem_releases_values_ref(self, ns, raw_redis):
        ns["df"] = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
        obj = ns.get_object("df")
        a_list = obj._get_column_list("a")
        assert raw_redis.exists(a_list.raw_key)
        del obj["a"]
        assert not raw_redis.exists(a_list.raw_key)

    def test_setitem_replace_category_with_category_releases_old_cats_ref(self, ns, raw_redis):
        # category -> category replace (different categories): old cats-ref released,
        # new ~cat:/~ord: overwritten (no HDEL needed).
        ns["df"] = pd.DataFrame({"c": pd.Series(["a", "b"], dtype=pd.CategoricalDtype(["a", "b"], ordered=True))})
        obj = ns.get_object("df")
        from gpdatacached.keys import parse_ref_encoded, make_raw_key
        from gpdatacached.types import encode_key

        ck = encode_key("c")
        old_cat_ref = raw_redis.hget(obj.raw_key, "~cat:" + ck)
        old_cat_name = parse_ref_encoded(old_cat_ref)
        old_cat_raw_key = make_raw_key("testdomain", "testns", old_cat_name)
        assert raw_redis.exists(old_cat_raw_key)
        # Replace with a new category set (different categories, unordered).
        obj["c"] = pd.Series(["x", "y"], dtype=pd.CategoricalDtype(["x", "y"], ordered=False))
        new_cat_ref = raw_redis.hget(obj.raw_key, "~cat:" + ck)
        assert new_cat_ref is not None
        assert new_cat_ref != old_cat_ref
        # The old categories ListObject should be gone (released + orphaned -> cascade-deleted).
        assert not raw_redis.exists(old_cat_raw_key)
        col = obj.value["c"]
        assert list(col.cat.categories) == ["x", "y"]
        assert col.cat.ordered is False

    def test_setitem_replace_non_category_with_category_writes_cat_fields(self, ns, raw_redis):
        # non-category -> category replace: ~cat:/~ord: get written on the replace path.
        ns["df"] = pd.DataFrame({"a": [1, 2]})
        obj = ns.get_object("df")
        from gpdatacached.types import encode_key

        ck = encode_key("a")
        assert not raw_redis.hexists(obj.raw_key, "~cat:" + ck)
        obj["a"] = pd.Series(["x", "y"], dtype=pd.CategoricalDtype(["x", "y"], ordered=True))
        assert raw_redis.hexists(obj.raw_key, "~cat:" + ck)
        assert raw_redis.hexists(obj.raw_key, "~ord:" + ck)
        col = obj.value["a"]
        assert list(col.cat.categories) == ["x", "y"]
        assert col.cat.ordered is True

    def test_delitem_category_column_releases_cats_ref(self, ns, raw_redis):
        # dropping a category column: ~cat: ref-release branch in __delitem__.
        ns["df"] = pd.DataFrame({"c": pd.Series(["a", "b"], dtype=pd.CategoricalDtype(["a", "b"], ordered=True))})
        obj = ns.get_object("df")
        from gpdatacached.types import encode_key, decode_element

        ck = encode_key("c")
        cat_ref = raw_redis.hget(obj.raw_key, "~cat:" + ck)
        cat_list = decode_element(cat_ref, ns)
        assert raw_redis.exists(cat_list.raw_key)
        del obj["c"]
        # The categories ListObject must be gone (released + orphaned -> cascade-deleted).
        assert not raw_redis.exists(cat_list.raw_key)
        assert not raw_redis.hexists(obj.raw_key, "~cat:" + ck)
        assert not raw_redis.hexists(obj.raw_key, "~ord:" + ck)


class TestNestingAndRefCounting:
    def test_dataframe_as_list_element(self, ns):
        ns["lst"] = [pd.DataFrame({"a": [1, 2]}), pd.DataFrame({"a": [3, 4]})]
        lst = ns.get_object("lst")
        assert list(lst[0].value["a"]) == [1, 2]
        assert list(lst[1].value["a"]) == [3, 4]

    def test_dataframe_as_dict_value(self, ns):
        ns["d"] = {"df": pd.DataFrame({"a": [1, 2]})}
        d = ns.get_object("d")
        assert list(d["df"].value["a"]) == [1, 2]

    def test_delete_named_dataframe_releases_all_children(self, ns, raw_redis):
        from gpdatacached.keys import make_raw_key

        ns["df"] = pd.DataFrame(
            {"a": [1, 2], "c": pd.Series(["x", "y"], dtype=pd.CategoricalDtype(["x", "y"]))},
            index=["r1", "r2"],
        )
        obj = ns.get_object("df")
        a_list = obj._get_column_list("a")
        c_list = obj._get_column_list("c")
        index_list = obj._get_level_list(0)
        # find the categories child by scanning the raw hash
        from gpdatacached.types import encode_key
        from gpdatacached.types import decode_element

        cat_ref = raw_redis.hget(obj.raw_key, "~cat:" + encode_key("c"))
        cat_list = decode_element(cat_ref, ns)
        for child in (a_list, c_list, index_list, cat_list):
            assert raw_redis.exists(child.raw_key)

        del ns["df"]

        for child in (a_list, c_list, index_list, cat_list):
            assert not raw_redis.exists(child.raw_key)
        assert not raw_redis.exists(make_raw_key("testdomain", "testns", "df"))

    def test_sliding_ttl_propagates_to_children(self, ns, raw_redis):
        ns["df"] = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
        obj = ns.get_object("df")
        obj.update_cache_mode_and_ttl("sliding", 300)
        a_list = obj._get_column_list("a")
        index_is_range = obj._index_kind() == "range"
        assert raw_redis.ttl(obj.raw_key) > 0
        assert raw_redis.ttl(a_list.raw_key) > 0
        if not index_is_range:
            assert raw_redis.ttl(obj._get_level_list(0).raw_key) > 0

    def test_sliding_ttl_propagates_to_labeled_index_children(self, ns, raw_redis):
        ns["df"] = pd.DataFrame({"a": [1, 2]}, index=["r1", "r2"])
        obj = ns.get_object("df")
        obj.update_cache_mode_and_ttl("sliding", 300)
        index_list = obj._get_level_list(0)
        assert raw_redis.ttl(obj.raw_key) > 0
        assert raw_redis.ttl(index_list.raw_key) > 0


class TestRejectedOperations:
    def test_value_setter_rejected(self, ns):
        ns["df"] = pd.DataFrame({"a": [1]})
        obj = ns.get_object("df")
        with pytest.raises(TypeError):
            obj.value = pd.DataFrame({"a": [9]})

    def test_iloc_setter_rejected(self, ns):
        ns["df"] = pd.DataFrame({"a": [1, 2]})
        obj = ns.get_object("df")
        with pytest.raises((TypeError, AttributeError)):
            obj.iloc[0] = {"a": 9}

    def test_loc_setter_rejected(self, ns):
        ns["df"] = pd.DataFrame({"a": [1]}, index=["r1"])
        obj = ns.get_object("df")
        with pytest.raises((TypeError, AttributeError)):
            obj.loc["r1"] = {"a": 9}


class TestPublicAPI:
    def test_register_is_idempotent(self):
        from gpdatacached.extensions.pandas_support import register
        from gpdatacached.types import GPDCTypeRegistry

        register()
        register()
        assert GPDCTypeRegistry.lookup_by_gpdc_type("collection:pandas_dataframe") is not None
        assert GPDCTypeRegistry.lookup_by_gpdc_type("collection:pandas_series") is not None


class TestFullWorkflow:
    def test_full_workflow(self, ns):
        ns["sales"] = pd.DataFrame(
            {"item": ["a", "b"], "qty": [1, 2], "price": [9.5, 3.0]},
            index=["mon", "tue"],
        )
        obj = ns.get_object("sales")

        # read whole
        assert list(obj.value.index) == ["mon", "tue"]

        # column access
        assert list(obj["qty"]) == [1, 2]

        # iloc row
        assert obj.iloc[0]["item"] == "a"

        # loc row
        assert obj.loc["tue"]["qty"] == 2

        # append rows
        obj.extend(pd.DataFrame({"item": ["c"], "qty": [3], "price": [1.0]}, index=["wed"]))
        assert list(obj.value.index) == ["mon", "tue", "wed"]

        # add column
        obj["discount"] = [0.1, 0.2, 0.0]
        assert list(obj.value.columns) == ["item", "qty", "price", "discount"]

        # drop column
        del obj["discount"]
        assert list(obj.value.columns) == ["item", "qty", "price"]

        # len
        assert len(obj) == 3

        # deepcopy
        cp = obj.deepcopy()
        assert isinstance(cp, pd.DataFrame)
        assert list(cp.index) == ["mon", "tue", "wed"]


class TestCreateElementBoundary:
    def test_create_dataframe_rejects_container_column(self, ns):
        from gpdatacached.extensions.pandas_support import (
            DataFrameCodec,
            UnsupportedElementTypeError,
        )

        with pytest.raises(UnsupportedElementTypeError):
            DataFrameCodec.create_raw(
                ns,
                "gpdc:testdomain:testns:raw:rej-df",
                pd.DataFrame({"o": [[1, 2], [3, 4]]}),
                "owner",
                "permanent",
                0,
            )

    def test_create_dataframe_rejects_dict_element(self, ns):
        from gpdatacached.extensions.pandas_support import (
            DataFrameCodec,
            UnsupportedElementTypeError,
        )

        with pytest.raises(UnsupportedElementTypeError):
            DataFrameCodec.create_raw(
                ns,
                "gpdc:testdomain:testns:raw:rej-df2",
                pd.DataFrame({"o": [{"a": 1}, {"b": 2}]}),
                "owner",
                "permanent",
                0,
            )

    def test_create_dataframe_rejects_container_index_label(self, ns):
        from gpdatacached.extensions.pandas_support import (
            DataFrameCodec,
            UnsupportedElementTypeError,
        )

        with pytest.raises(UnsupportedElementTypeError):
            DataFrameCodec.create_raw(
                ns,
                "gpdc:testdomain:testns:raw:rej-df-idx",
                pd.DataFrame({"a": [1, 2]}, index=pd.Index([["x"], ["y"]], dtype=object)),
                "owner",
                "permanent",
                0,
            )


class TestExtendElementBoundary:
    def test_dataframe_extend_rejects_container_row(self, ns):
        from gpdatacached.extensions.pandas_support import UnsupportedElementTypeError

        ns["df"] = pd.DataFrame({"o": ["a", "b"]}, dtype=object)
        obj = ns.get_object("df")
        with pytest.raises(UnsupportedElementTypeError):
            obj.extend(pd.DataFrame({"o": [[1, 2]]}))
        assert list(obj["o"]) == ["a", "b"]

    def test_dataframe_extend_rejects_container_index_label(self, ns):
        from gpdatacached.extensions.pandas_support import UnsupportedElementTypeError

        ns["df"] = pd.DataFrame({"o": ["a", "b"]}, index=["r1", "r2"], dtype=object)
        obj = ns.get_object("df")
        with pytest.raises(UnsupportedElementTypeError):
            obj.extend(pd.DataFrame({"o": ["z"]}, index=pd.Index([["nested"]], dtype=object)))
        # object untouched: values and index unchanged
        assert list(obj["o"]) == ["a", "b"]
        assert list(obj.value.index) == ["r1", "r2"]


class TestSetItemElementBoundary:
    def test_setitem_rejects_container_column(self, ns):
        from gpdatacached.extensions.pandas_support import UnsupportedElementTypeError

        ns["df"] = pd.DataFrame({"a": [1, 2]})
        obj = ns.get_object("df")
        with pytest.raises(UnsupportedElementTypeError):
            obj["b"] = [[1, 2], [3, 4]]
        # column not added
        assert list(obj.columns) == ["a"]

    def test_setitem_replace_rejects_container_preserves_column(self, ns):
        from gpdatacached.extensions.pandas_support import UnsupportedElementTypeError

        ns["df"] = pd.DataFrame({"a": ["x", "y"]}, dtype=object)
        obj = ns.get_object("df")
        with pytest.raises(UnsupportedElementTypeError):
            obj["a"] = [[1, 2], [3, 4]]
        # old column values intact (ref not released, no partial mutation)
        assert list(obj["a"]) == ["x", "y"]

    def test_setitem_accepts_tuple_column(self, ns):
        ns["df"] = pd.DataFrame({"a": [1, 2]})
        obj = ns.get_object("df")
        obj["b"] = [(1, 2), (3, 4)]
        assert list(obj.columns) == ["a", "b"]
        assert list(obj["b"]) == [(1, 2), (3, 4)]


class TestReadPathScalarCells:
    def test_iloc_object_column_returns_scalar(self, ns):
        ns["df"] = pd.DataFrame({"o": ["a", "b"]})
        obj = ns.get_object("df")
        row = obj.iloc[0]
        assert row["o"] == "a"
        assert type(row["o"]) is str

    def test_loc_object_column_returns_scalar(self, ns):
        ns["df"] = pd.DataFrame({"o": ["a", "b"]}, index=["r1", "r2"])
        obj = ns.get_object("df")
        row = obj.loc["r1"]
        assert row["o"] == "a"

    def test_positional_slice_object_column_returns_scalars(self, ns):
        ns["df"] = pd.DataFrame({"o": ["a", "b", "c"]})
        obj = ns.get_object("df")
        sub = obj.iloc[0:2]
        assert list(sub["o"]) == ["a", "b"]


class TestDataFrameMultiIndexRowRead:
    def _make(self, ns):
        idx = pd.MultiIndex.from_tuples([("a", 1), ("a", 2), ("b", 1)], names=["L0", "L1"])
        ns["mdf"] = pd.DataFrame({"x": [1, 2, 3], "y": [4, 5, 6]}, index=idx)
        return ns.get_object("mdf")

    def test_iloc_int_returns_row_series(self, ns):
        obj = self._make(ns)
        row = obj.iloc[0]
        assert row["x"] == 1 and row["y"] == 4

    def test_iloc_slice_returns_dataframe(self, ns):
        obj = self._make(ns)
        result = obj.iloc[1:3]
        assert list(result["x"]) == [2, 3]
        assert isinstance(result.index, pd.MultiIndex)
        assert list(result.index) == [("a", 2), ("b", 1)]

    def test_loc_full_tuple_one_match_returns_series(self, ns):
        obj = self._make(ns)
        row = obj.loc[("b", 1)]
        assert row["x"] == 3
        assert row["y"] == 6

    def test_loc_depth1_returns_dataframe(self, ns):
        obj = self._make(ns)
        result = obj.loc["a"]
        assert list(result["x"]) == [1, 2]
        assert isinstance(result.index, pd.MultiIndex)

    def test_loc_missing_label_raises_keyerror(self, ns):
        obj = self._make(ns)
        with pytest.raises(KeyError):
            obj.loc[("zzz", 9)]

    def test_loc_slice_rejected_points_to_value(self, ns):
        obj = self._make(ns)
        with pytest.raises(TypeError) as exc:
            obj.loc["a":"b"]
        assert ".value" in str(exc.value)

    def test_loc_list_rejected_points_to_value(self, ns):
        obj = self._make(ns)
        with pytest.raises(TypeError) as exc:
            obj.loc[["a", "b"]]
        assert ".value" in str(exc.value)

    def test_loc_full_tuple_duplicate_returns_dataframe(self, ns):
        idx = pd.MultiIndex.from_tuples([("a", 1), ("a", 1), ("b", 2)], names=["L0", "L1"])
        ns["dup"] = pd.DataFrame({"x": [10, 20, 30]}, index=idx)
        result = ns.get_object("dup").loc[("a", 1)]
        assert isinstance(result, pd.DataFrame)
        assert list(result["x"]) == [10, 20]

    def test_iloc_negative_int_returns_row_series(self, ns):
        obj = self._make(ns)
        row = obj.iloc[-1]
        assert row["x"] == 3 and row["y"] == 6

    def test_loc_on_l1_labelled_df_returns_row_series(self, ns):
        ns["l1"] = pd.DataFrame({"x": [10, 20, 30]}, index=["p", "q", "r"])
        obj = ns.get_object("l1")
        row = obj.loc["q"]
        assert isinstance(row, pd.Series)
        assert row["x"] == 20
        assert row.name == "q"


class TestDataFrameMultiIndexRowExtend:
    def _make(self, ns):
        idx = pd.MultiIndex.from_tuples([("a", 1), ("a", 2)], names=["L0", "L1"])
        ns["mdf"] = pd.DataFrame({"x": [1, 2]}, index=idx)
        return ns.get_object("mdf")

    def test_extend_with_matching_dataframe(self, ns):
        obj = self._make(ns)
        obj.extend(pd.DataFrame({"x": [3, 4]}, index=pd.MultiIndex.from_tuples([("b", 1), ("b", 2)])))
        assert list(obj.value["x"]) == [1, 2, 3, 4]
        assert list(obj.value.index) == [("a", 1), ("a", 2), ("b", 1), ("b", 2)]

    def test_extend_level_count_mismatch_raises(self, ns):
        obj = self._make(ns)
        with pytest.raises(ValueError, match="levels"):
            obj.extend(pd.DataFrame({"x": [9]}, index=["only"]))

    def test_extend_dict_rejected_on_l2(self, ns):
        obj = self._make(ns)
        with pytest.raises(TypeError, match=".value"):
            obj.extend({"x": [9]})

    def test_extend_list_of_dicts_rejected_on_l2(self, ns):
        obj = self._make(ns)
        with pytest.raises(TypeError):
            obj.extend([{"x": 9}])

    def test_extend_dict_still_works_on_l1_levels(self, ns):
        ns["df1"] = pd.DataFrame({"x": [1, 2]}, index=["p", "q"])
        ns.get_object("df1").extend({"x": [3]})  # L=1 levels keeps today's behaviour
        assert list(ns.get_object("df1").value["x"]) == [1, 2, 3]

    def test_extend_range_then_incremental_heterogeneous_labels(self, ns):
        # the Task-5 honest-dtype regression, DataFrame variant
        ns["rs"] = pd.DataFrame({"x": [1, 2, 3]})  # range
        obj = ns.get_object("rs")
        obj.extend(pd.DataFrame({"x": [4]}, index=["x"]))
        obj.extend(pd.DataFrame({"x": [5]}, index=["y"]))  # must NOT fail
        result = obj.value
        assert list(result["x"]) == [1, 2, 3, 4, 5]
        assert list(result.index) == [0, 1, 2, "x", "y"]

    def test_extend_range_with_multilevel_input_raises_without_promote(self, ns):
        ns["rs"] = pd.DataFrame({"x": [1, 2]})  # range
        obj = ns.get_object("rs")
        raw_before = ns._redis().hgetall(obj.raw_key)
        with pytest.raises(ValueError, match="levels"):
            obj.extend(pd.DataFrame({"x": [9]}, index=pd.MultiIndex.from_tuples([("a", 1)])))
        raw_after = ns._redis().hgetall(obj.raw_key)
        assert raw_after.get("~index_kind") == raw_before.get("~index_kind")  # still range
        assert "~index_l0" not in raw_after

    def test_extend_multilevel_dataframe_with_category_column(self, ns):
        idx = pd.MultiIndex.from_tuples([("a", 1), ("a", 2)], names=["L0", "L1"])
        ns["catdf"] = pd.DataFrame(
            {"c": pd.Categorical(["x", "y"], categories=["x", "y", "z"], ordered=True)},
            index=idx,
        )
        obj = ns.get_object("catdf")
        obj.extend(
            pd.DataFrame(
                {"c": pd.Categorical(["z", "x"], categories=["x", "y", "z"], ordered=True)},
                index=pd.MultiIndex.from_tuples([("b", 1), ("b", 2)]),
            )
        )
        result = obj.value
        assert list(result["c"]) == ["x", "y", "z", "x"]
        assert list(result["c"].cat.categories) == ["x", "y", "z"]
        assert result["c"].cat.ordered is True
        assert list(result.index) == [("a", 1), ("a", 2), ("b", 1), ("b", 2)]


class TestDataFrameColumnMultiIndexStorage:
    def test_two_level_column_index_round_trip_preserves_names(self, ns):
        from gpdatacached.extensions.pandas_support import DataFrameCodec

        cols = pd.MultiIndex.from_tuples([("a", "x"), ("a", "y"), ("b", "x")], names=["L0", "L1"])
        original = pd.DataFrame([[1, 2, 3], [4, 5, 6]], columns=cols)
        raw_key = "gpdc:testdomain:testns:raw:cmi"
        try:
            DataFrameCodec.create_raw(ns, raw_key, original, "owner", "permanent", 0)
            result = DataFrameCodec.read_raw(ns, raw_key)
        finally:
            ns._redis().delete(raw_key)
        assert isinstance(result.columns, pd.MultiIndex)
        assert list(result.columns) == list(original.columns)
        assert result.columns.names == ("L0", "L1")
        assert list(result[("a", "x")]) == [1, 4]

    def test_raw_hash_has_columns_names_field(self, ns):
        from gpdatacached.extensions.pandas_support import DataFrameCodec

        cols = pd.MultiIndex.from_tuples([("a", "x"), ("b", "y")], names=["L0", "L1"])
        original = pd.DataFrame([[1, 2]], columns=cols)
        raw_key = "gpdc:testdomain:testns:raw:cmihash"
        try:
            DataFrameCodec.create_raw(ns, raw_key, original, "owner", "permanent", 0)
            assert ns._redis().hget(raw_key, "~columns_names").startswith("tuple:")
            assert ns._redis().hget(raw_key, "~columns").startswith("tuple:")
        finally:
            ns._redis().delete(raw_key)

    def test_single_level_columns_have_no_columns_names_field(self, ns):
        from gpdatacached.extensions.pandas_support import DataFrameCodec

        original = pd.DataFrame({"x": [1, 2], "y": [3, 4]})
        raw_key = "gpdc:testdomain:testns:raw:csingle"
        try:
            DataFrameCodec.create_raw(ns, raw_key, original, "owner", "permanent", 0)
            assert ns._redis().hget(raw_key, "~columns_names") is None
        finally:
            ns._redis().delete(raw_key)

    def test_three_level_column_index_round_trip(self, ns):
        from gpdatacached.extensions.pandas_support import DataFrameCodec

        cols = pd.MultiIndex.from_tuples([("a", "x", 1), ("a", "y", 2), ("b", "x", 3)], names=["L0", "L1", "L2"])
        original = pd.DataFrame([[10, 20, 30]], columns=cols)
        raw_key = "gpdc:testdomain:testns:raw:cmi3"
        try:
            DataFrameCodec.create_raw(ns, raw_key, original, "owner", "permanent", 0)
            result = DataFrameCodec.read_raw(ns, raw_key)
        finally:
            ns._redis().delete(raw_key)
        assert isinstance(result.columns, pd.MultiIndex)
        assert result.columns.nlevels == 3
        assert list(result.columns) == list(original.columns)
        assert result.columns.names == ("L0", "L1", "L2")
        assert list(result[("b", "x", 3)]) == [30]


class TestDataFrameColumnMultiIndexGetitem:
    def _make(self, ns):
        cols = pd.MultiIndex.from_tuples([("a", "x"), ("a", "y"), ("b", "x")], names=["L0", "L1"])
        ns["cdf"] = pd.DataFrame([[1, 2, 3], [4, 5, 6]], columns=cols)
        return ns.get_object("cdf")

    def test_full_tuple_returns_series(self, ns):
        obj = self._make(ns)
        s = obj[("a", "x")]
        assert isinstance(s, pd.Series)
        assert list(s) == [1, 4]

    def test_level0_scalar_returns_sub_dataframe(self, ns):
        obj = self._make(ns)
        sub = obj["a"]
        assert isinstance(sub, pd.DataFrame)
        assert list(sub.columns) == [("a", "x"), ("a", "y")]
        assert list(sub[("a", "x")]) == [1, 4]

    def test_partial_tuple_returns_sub_dataframe(self, ns):
        cols = pd.MultiIndex.from_tuples([("a", "x", 1), ("a", "x", 2), ("a", "y", 1)], names=["L0", "L1", "L2"])
        ns["c3"] = pd.DataFrame([[1, 2, 3]], columns=cols)
        sub = ns.get_object("c3")[("a", "x")]
        assert isinstance(sub, pd.DataFrame)
        assert list(sub.columns) == [("a", "x", 1), ("a", "x", 2)]

    def test_list_of_tuples_returns_sub_dataframe(self, ns):
        obj = self._make(ns)
        sub = obj[[("a", "x"), ("b", "x")]]
        assert isinstance(sub, pd.DataFrame)
        assert list(sub.columns) == [("a", "x"), ("b", "x")]

    def test_list_of_level0_scalars_returns_sub_dataframe(self, ns):
        obj = self._make(ns)
        sub = obj[["a", "b"]]
        assert isinstance(sub, pd.DataFrame)
        assert list(sub.columns) == [("a", "x"), ("a", "y"), ("b", "x")]

    def test_missing_full_tuple_raises_keyerror(self, ns):
        obj = self._make(ns)
        with pytest.raises(KeyError):
            obj[("zzz", "q")]

    def test_unknown_level0_scalar_raises_keyerror(self, ns):
        obj = self._make(ns)
        with pytest.raises(KeyError):
            obj["zzz"]

    def test_over_depth_tuple_raises_keyerror(self, ns):
        obj = self._make(ns)  # 2-level columns
        with pytest.raises(KeyError):
            obj[("a", "x", "extra")]

    def test_empty_list_returns_empty_dataframe(self, ns):
        obj = self._make(ns)  # 2-level column MultiIndex
        sub = obj[[]]
        assert isinstance(sub, pd.DataFrame)
        assert len(sub.columns) == 0
        assert sub.columns.nlevels == 2
        assert sub.columns.names == ("L0", "L1")
        assert len(sub) == len(obj)

    def test_single_level_empty_list(self, ns):
        ns["sl"] = pd.DataFrame({"x": [1, 2], "y": [3, 4]})
        sub = ns.get_object("sl")[[]]
        assert isinstance(sub, pd.DataFrame)
        assert list(sub.columns) == []

    def test_mixed_type_list_selection(self, ns):
        obj = self._make(ns)  # columns [("a","x"),("a","y"),("b","x")]
        sub = obj[[("a", "x"), "b"]]
        assert list(sub.columns) == [("a", "x"), ("b", "x")]

    def test_list_over_depth_item_raises_keyerror(self, ns):
        obj = self._make(ns)  # 2-level columns
        with pytest.raises(KeyError):
            obj[[("a", "x", "extra")]]


class TestDataFrameColumnMultiIndexSetDel:
    def _make(self, ns):
        cols = pd.MultiIndex.from_tuples([("a", "x"), ("a", "y")], names=["L0", "L1"])
        ns["cdf"] = pd.DataFrame([[1, 2], [3, 4]], columns=cols)
        return ns.get_object("cdf")

    def test_setitem_new_full_tuple_adds_column(self, ns):
        obj = self._make(ns)
        obj[("b", "z")] = [5, 6]
        result = obj.value
        assert list(result.columns) == [("a", "x"), ("a", "y"), ("b", "z")]
        assert list(result[("b", "z")]) == [5, 6]
        assert result.columns.names == ("L0", "L1")

    def test_setitem_replace_existing_full_tuple(self, ns):
        obj = self._make(ns)
        obj[("a", "x")] = [9, 9]
        assert list(obj.value[("a", "x")]) == [9, 9]

    def test_setitem_arity_mismatch_raises_valueerror(self, ns):
        obj = self._make(ns)  # 2-level columns
        with pytest.raises(ValueError, match="levels"):
            obj[("only_one",)] = [1, 2]

    def test_setitem_partial_scalar_rejected(self, ns):
        obj = self._make(ns)
        with pytest.raises(TypeError, match=".value"):
            obj["a"] = [1, 2]

    def test_setitem_list_rejected(self, ns):
        obj = self._make(ns)
        with pytest.raises(TypeError, match=".value"):
            obj[[("a", "x"), ("a", "y")]] = [[1, 2], [3, 4]]

    def test_setitem_wrong_length_raises_valueerror(self, ns):
        obj = self._make(ns)  # 2 rows
        with pytest.raises(ValueError, match="row count"):
            obj[("b", "z")] = [1, 2, 3]

    def test_delitem_full_tuple_drops_column(self, ns):
        obj = self._make(ns)
        del obj[("a", "x")]
        result = obj.value
        assert list(result.columns) == [("a", "y")]
        assert result.columns.names == ("L0", "L1")

    def test_delitem_arity_mismatch_raises_valueerror(self, ns):
        obj = self._make(ns)  # 2-level columns
        with pytest.raises(ValueError, match="levels"):
            del obj[("only_one",)]

    def test_delitem_partial_scalar_rejected(self, ns):
        obj = self._make(ns)
        with pytest.raises(TypeError, match=".value"):
            del obj["a"]

    def test_delitem_list_rejected(self, ns):
        obj = self._make(ns)
        with pytest.raises(TypeError, match=".value"):
            del obj[[("a", "x"), ("a", "y")]]

    def test_single_level_setitem_tuple_rejected(self, ns):
        ns["sl"] = pd.DataFrame({"x": [1, 2]})
        with pytest.raises(TypeError, match=".value"):
            ns.get_object("sl")[("a", "b")] = [3, 4]

    def test_single_level_delitem_tuple_rejected(self, ns):
        ns["sl"] = pd.DataFrame({"x": [1, 2], "y": [3, 4]})
        with pytest.raises(TypeError, match=".value"):
            del ns.get_object("sl")[("a", "b")]


class TestDataFrameMultiIndexIntegration:
    def test_row_and_column_multiindex_compose(self, ns):
        rows = pd.MultiIndex.from_tuples([("r", 1), ("r", 2), ("s", 1)], names=["RL0", "RL1"])
        cols = pd.MultiIndex.from_tuples([("a", "x"), ("b", "y")], names=["CL0", "CL1"])
        original = pd.DataFrame([[1, 2], [3, 4], [5, 6]], index=rows, columns=cols)
        ns["both"] = original
        result = ns["both"].value
        assert isinstance(result.index, pd.MultiIndex)
        assert isinstance(result.columns, pd.MultiIndex)
        assert result.index.names == ("RL0", "RL1")
        assert result.columns.names == ("CL0", "CL1")
        assert list(result[("a", "x")]) == [1, 3, 5]

    def test_loc_row_partial_on_rowcol_multiindex_df(self, ns):
        rows = pd.MultiIndex.from_tuples([("r", 1), ("r", 2), ("s", 1)], names=["RL0", "RL1"])
        cols = pd.MultiIndex.from_tuples([("a", "x"), ("a", "y")], names=["CL0", "CL1"])
        ns["both"] = pd.DataFrame([[1, 2], [3, 4], [5, 6]], index=rows, columns=cols)
        obj = ns.get_object("both")
        sub = obj.loc["r"]  # row-axis depth-1 partial (Plan 1) — supported
        assert list(sub.index) == [("r", 1), ("r", 2)]

    def test_getitem_column_partial_on_rowcol_multiindex_df(self, ns):
        rows = pd.MultiIndex.from_tuples([("r", 1), ("s", 1)], names=["RL0", "RL1"])
        cols = pd.MultiIndex.from_tuples([("a", "x"), ("a", "y"), ("b", "x")], names=["CL0", "CL1"])
        ns["both"] = pd.DataFrame([[1, 2, 3], [4, 5, 6]], index=rows, columns=cols)
        obj = ns.get_object("both")
        sub = obj["a"]  # column-axis depth-1 partial (Plan 2) — supported
        assert list(sub.columns) == [("a", "x"), ("a", "y")]
        assert isinstance(sub.index, pd.MultiIndex)

    def test_extend_column_multiindex_dataframe(self, ns):
        cols = pd.MultiIndex.from_tuples([("a", "x"), ("a", "y")], names=["CL0", "CL1"])
        ns["cdf"] = pd.DataFrame([[1, 2]], columns=cols)
        obj = ns.get_object("cdf")
        more = pd.DataFrame([[3, 4]], columns=cols, index=[1])
        obj.extend(more)
        result = obj.value
        assert list(result[("a", "x")]) == [1, 3]
        assert list(result[("a", "y")]) == [2, 4]
        assert list(result.columns) == [("a", "x"), ("a", "y")]
        assert result.columns.names == ("CL0", "CL1")

    def test_column_multiindex_cascade_delete_releases_columns(self, ns):
        cols = pd.MultiIndex.from_tuples([("a", "x"), ("b", "y")], names=["CL0", "CL1"])
        ns["wrap"] = {"d": pd.DataFrame([[1, 2]], columns=cols)}
        df_obj = ns.get_object("wrap")["d"]
        r = df_obj._redis()
        cx_key = r.hget(df_obj.raw_key, encode_key(("a", "x")))[len("ref:") :]
        cy_key = r.hget(df_obj.raw_key, encode_key(("b", "y")))[len("ref:") :]
        assert r.exists(cx_key, cy_key) == 2
        del ns["wrap"]
        assert r.exists(cx_key, cy_key) == 0

    def test_column_multiindex_level_children_get_ttl(self, domain_with_namespaces):
        ns = domain_with_namespaces.ensure_namespace("ns_2")  # sliding, 7200s
        cols = pd.MultiIndex.from_tuples([("a", "x"), ("b", "y")], names=["CL0", "CL1"])
        ns["ct"] = pd.DataFrame([[1, 2]], columns=cols)
        df_obj = ns.get_object("ct")
        r = df_obj._redis()
        for c in [("a", "x"), ("b", "y")]:
            assert r.ttl(df_obj._get_column_list(c).raw_key) > 0

    def _make_dual(self, ns):
        rows = pd.MultiIndex.from_tuples([("r", 1), ("r", 2), ("s", 1)], names=["RL0", "RL1"])
        cols = pd.MultiIndex.from_tuples([("a", "x"), ("a", "y"), ("b", "x")], names=["CL0", "CL1"])
        ns["dual"] = pd.DataFrame([[1, 2, 3], [4, 5, 6], [7, 8, 9]], index=rows, columns=cols)
        return ns.get_object("dual")

    def test_iloc_slice_preserves_column_multiindex(self, ns):
        obj = self._make_dual(ns)
        sub = obj.iloc[0:2]
        assert isinstance(sub.columns, pd.MultiIndex)
        assert sub.columns.names == ("CL0", "CL1")
        assert list(sub[("a", "x")]) == [1, 4]
        assert isinstance(sub.index, pd.MultiIndex)

    def test_iloc_int_row_series_has_column_multiindex(self, ns):
        obj = self._make_dual(ns)
        row = obj.iloc[1]
        assert isinstance(row, pd.Series)
        assert isinstance(row.index, pd.MultiIndex)
        assert row.index.names == ("CL0", "CL1")
        assert row[("a", "y")] == 5
        # row label preserved
        assert row.name == ("r", 2)

    def test_loc_full_tuple_preserves_column_multiindex(self, ns):
        obj = self._make_dual(ns)
        sub = obj.loc[("r", 1)]
        assert isinstance(sub, pd.Series)  # single full-depth row match -> Series
        assert isinstance(sub.index, pd.MultiIndex)
        assert sub.index.names == ("CL0", "CL1")
        assert sub[("b", "x")] == 3

    def test_loc_partial_preserves_column_multiindex(self, ns):
        obj = self._make_dual(ns)
        sub = obj.loc["r"]  # row depth-1 partial -> DataFrame
        assert isinstance(sub.columns, pd.MultiIndex)
        assert sub.columns.names == ("CL0", "CL1")
        assert list(sub.index) == [("r", 1), ("r", 2)]


class TestStaleHandleLogic02DataFrame:
    """CQR-2026-07-24 logic.02: a stale DataFrame handle must raise before any Redis write."""

    def _delete_meta(self, obj, raw_redis):
        raw_redis.delete(obj._canonical_redis_key())

    def test_extend_raises_and_leaves_raw_untouched(self, ns, raw_redis):
        from gpdatacached.exceptions import ObjectGoneError

        ns["df"] = pd.DataFrame({"v": [1, 2]}, index=pd.RangeIndex(2))
        obj = ns.get_object("df")
        before = raw_redis.hgetall(obj.raw_key)
        self._delete_meta(obj, raw_redis)
        with pytest.raises(ObjectGoneError, match="no longer exists"):
            obj.extend(pd.DataFrame({"v": [3, 4]}, index=pd.RangeIndex(2, 4)))
        assert raw_redis.hgetall(obj.raw_key) == before

    def test_setitem_raises_and_leaves_raw_untouched(self, ns, raw_redis):
        from gpdatacached.exceptions import ObjectGoneError

        ns["df"] = pd.DataFrame({"v": [1, 2]}, index=pd.RangeIndex(2))
        obj = ns.get_object("df")
        before = raw_redis.hgetall(obj.raw_key)
        self._delete_meta(obj, raw_redis)
        with pytest.raises(ObjectGoneError, match="no longer exists"):
            obj["w"] = [10, 20]
        assert raw_redis.hgetall(obj.raw_key) == before

    def test_delitem_raises_and_leaves_raw_untouched(self, ns, raw_redis):
        from gpdatacached.exceptions import ObjectGoneError

        ns["df"] = pd.DataFrame({"v": [1, 2], "w": [3, 4]}, index=pd.RangeIndex(2))
        obj = ns.get_object("df")
        before = raw_redis.hgetall(obj.raw_key)
        self._delete_meta(obj, raw_redis)
        with pytest.raises(ObjectGoneError, match="no longer exists"):
            del obj["w"]
        assert raw_redis.hgetall(obj.raw_key) == before


class TestFailedEncodeLogic01DataFrame:
    """CQR-2026-07-24 logic.01: a failing DataFrame __setitem__ (column replace
    whose value fails to encode) must not release the old column ref early."""

    def test_setitem_replacing_column_with_cyclic_value_leaves_old_intact(self, ns, raw_redis):
        # Build a DataFrame with a container column whose replacement would fail.
        # Simplest failing-encode: a self-referential list (cycle) as the new value.
        ns["df"] = pd.DataFrame({"v": [1, 2]}, index=pd.RangeIndex(2))
        obj = ns.get_object("df")
        before = raw_redis.hgetall(obj.raw_key)

        cyclic = []
        cyclic.append(cyclic)  # self-reference -> CyclicReferenceError on encode
        with pytest.raises(Exception):
            obj["v"] = cyclic
        # Parent hash unchanged; the old column 'v' was NOT released/deleted.
        assert raw_redis.hgetall(obj.raw_key) == before


