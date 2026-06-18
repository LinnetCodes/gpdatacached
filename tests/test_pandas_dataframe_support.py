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
