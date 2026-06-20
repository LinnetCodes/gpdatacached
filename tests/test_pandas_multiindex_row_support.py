from __future__ import annotations

import threading

import pandas as pd
import pytest

from gpdatacached.types import GPDCTypeRegistry, encode_key


@pytest.fixture(autouse=True)
def _register_pandas():
    """Isolate registry and register the pandas Series/DataFrame types for every test."""
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


class TestUnsupportedLevels:
    def test_sparse_level_series_rejected(self, ns):
        from gpdatacached.extensions.pandas_support import UnsupportedSeriesDtypeError

        idx = pd.MultiIndex.from_arrays([["a", "b"], pd.array([1, 2], dtype="Sparse[int64]")])
        with pytest.raises(UnsupportedSeriesDtypeError):
            ns["bad"] = pd.Series([1, 2], index=idx)

    def test_sparse_level_dataframe_rejected(self, ns):
        from gpdatacached.extensions.pandas_support import UnsupportedDataFrameError

        idx = pd.MultiIndex.from_arrays([["a", "b"], pd.array([1, 2], dtype="Sparse[int64]")])
        with pytest.raises(UnsupportedDataFrameError):
            ns["baddf"] = pd.DataFrame({"x": [1, 2]}, index=idx)

    def test_unsupported_level_error_message_guides_user(self, ns):
        from gpdatacached.extensions.pandas_support import UnsupportedSeriesDtypeError

        idx = pd.MultiIndex.from_arrays([["a", "b"], pd.array([1, 2], dtype="Sparse[int64]")])
        with pytest.raises(UnsupportedSeriesDtypeError) as exc_info:
            ns["bad2"] = pd.Series([1, 2], index=idx)
        msg = str(exc_info.value)
        assert "GPDCTypeRegistry.register" in msg


class TestMixedLevelInferredAsObject:
    def test_mixed_level_series_round_trips_as_object(self, ns):
        idx = pd.MultiIndex.from_arrays([["a", "b"], [1, "two"]], names=["L0", "L1"])
        ns["mix"] = pd.Series([1, 2], index=idx)
        result = ns["mix"].value
        assert str(result.index.get_level_values(1).dtype) == "object"
        assert list(result.index.get_level_values(1)) == [1, "two"]

    def test_mixed_level_dataframe_round_trips_as_object(self, ns):
        idx = pd.MultiIndex.from_arrays([["a", "b"], [1, "two"]], names=["L0", "L1"])
        ns["mixdf"] = pd.DataFrame({"x": [1, 2]}, index=idx)
        result = ns["mixdf"].value
        assert str(result.index.get_level_values(1).dtype) == "object"


class TestMultiIndexNestingAndRefcount:
    def test_multiindex_series_inside_dict_cascade_deletes_levels(self, ns):
        idx = pd.MultiIndex.from_tuples([("a", 1), ("a", 2), ("b", 1)], names=["L0", "L1"])
        ns["wrap"] = {"s": pd.Series([10, 20, 30], index=idx)}
        s_obj = ns.get_object("wrap")["s"]
        r = s_obj._redis()
        data_key = r.hget(s_obj.raw_key, "data")[len("ref:") :]
        l0_key = r.hget(s_obj.raw_key, "index_l0")[len("ref:") :]
        l1_key = r.hget(s_obj.raw_key, "index_l1")[len("ref:") :]
        assert r.exists(data_key, l0_key, l1_key) == 3
        del ns["wrap"]
        # cascade-delete must reach the data child AND both level children
        assert r.exists(data_key, l0_key, l1_key) == 0

    def test_multiindex_dataframe_inside_list_cascade_deletes_levels(self, ns):
        idx = pd.MultiIndex.from_tuples([("a", 1), ("b", 2)], names=["L0", "L1"])
        ns["wrap"] = [pd.DataFrame({"x": [1, 2]}, index=idx)]
        df_obj = ns.get_object("wrap")[0]
        r = df_obj._redis()
        col_key = r.hget(df_obj.raw_key, encode_key("x"))[len("ref:") :]
        l0_key = r.hget(df_obj.raw_key, "~index_l0")[len("ref:") :]
        l1_key = r.hget(df_obj.raw_key, "~index_l1")[len("ref:") :]
        assert r.exists(col_key, l0_key, l1_key) == 3
        del ns["wrap"]
        assert r.exists(col_key, l0_key, l1_key) == 0

    def test_multiindex_series_level_children_get_ttl(self, domain_with_namespaces):
        ns = domain_with_namespaces.ensure_namespace("ns_2")  # sliding, 7200s
        idx = pd.MultiIndex.from_tuples([("a", 1), ("b", 2)], names=["L0", "L1"])
        ns["st"] = pd.Series([1, 2], index=idx)
        s_obj = ns.get_object("st")
        r = s_obj._redis()
        # create-time sliding-TTL must propagate to every level child
        for i in range(2):
            assert r.ttl(s_obj._get_level_list(i).raw_key) > 0

    def test_multiindex_dataframe_level_children_get_ttl(self, domain_with_namespaces):
        ns = domain_with_namespaces.ensure_namespace("ns_2")  # sliding, 7200s
        idx = pd.MultiIndex.from_tuples([("a", 1), ("b", 2)], names=["L0", "L1"])
        ns["dt"] = pd.DataFrame({"x": [1, 2]}, index=idx)
        df_obj = ns.get_object("dt")
        r = df_obj._redis()
        for i in range(2):
            assert r.ttl(df_obj._get_level_list(i).raw_key) > 0
