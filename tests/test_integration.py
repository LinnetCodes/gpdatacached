# tests/test_integration.py
"""End-to-end tests based on spec section 12 (Complete Data Flow Examples)."""

import datetime
import pytest
from gpdatacached.keys import make_object_key


class TestPublicAPI:
    """Verify public API exports from gpdatacached."""

    def test_imports(self):
        import gpdatacached

        assert hasattr(gpdatacached, "__version__")
        assert hasattr(gpdatacached, "GPDCDomain")
        assert hasattr(gpdatacached, "GPDCNamespace")
        assert hasattr(gpdatacached, "GPDCDomainConfig")
        assert hasattr(gpdatacached, "GPDCNamespaceConfig")
        assert hasattr(gpdatacached, "GPDCScalarCodec")
        assert hasattr(gpdatacached, "IntObject")
        assert hasattr(gpdatacached, "StrObject")
        assert hasattr(gpdatacached, "BoolObject")
        assert hasattr(gpdatacached, "FloatObject")
        assert hasattr(gpdatacached, "DateTimeObject")
        assert hasattr(gpdatacached, "DateObject")
        assert hasattr(gpdatacached, "TimeObject")
        assert hasattr(gpdatacached, "NoneObject")


class TestCopySeparateRedisKeys:
    def test_copy_creates_separate_redis_keys(self, ns, clean_redis):
        ns["a"] = 10
        ns["b"] = ns["a"]
        key_a = make_object_key("testdomain", "testns", "a")
        key_b = make_object_key("testdomain", "testns", "b")
        assert clean_redis.exists(key_a) == 1
        assert clean_redis.exists(key_b) == 1
        assert key_a != key_b


class TestAllScalarTypes:
    """All 8 scalar types round-trip through namespace."""

    @pytest.mark.parametrize(
        "key,value",
        [
            ("i", 42),
            ("f", 3.14),
            ("s", "hello world"),
            ("s_colon", "hello:world"),
            ("b_true", True),
            ("b_false", False),
            ("dt", datetime.datetime(2024, 6, 15, 9, 0, 0)),
            ("d", datetime.date(2024, 6, 15)),
            ("t", datetime.time(9, 30, 0)),
            ("n", None),
        ],
    )
    def test_roundtrip(self, ns, key, value):
        ns[key] = value
        result = ns[key]
        assert result == value
        assert type(result) is type(value)
