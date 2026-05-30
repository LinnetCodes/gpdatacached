# tests/test_integration.py
"""End-to-end tests based on spec section 12 (Complete Data Flow Examples)."""

import datetime
import pytest
from gpdatacached.keys import GLOBAL_META_KEY, make_domain_meta_key, make_namespace_meta_key, make_object_key


class TestSpecSection12CreateScalarObject:
    """Spec section 12: Creating a scalar object."""

    def test_full_redis_state_after_create(self, domain, clean_redis):
        ns = domain.get_namespace("myns")
        ns["counter"] = 42

        # Global meta
        assert clean_redis.hget(GLOBAL_META_KEY, "version") == "0.1.0"
        assert "testdomain" in clean_redis.smembers("gpdc:__domains__")

        # Domain meta
        domain_meta = clean_redis.hgetall(make_domain_meta_key("testdomain"))
        assert domain_meta["default_cache_mode"] == "permanent"

        # Domain namespaces set
        assert "myns" in clean_redis.smembers("gpdc:testdomain:__namespaces__")

        # Namespace meta
        ns_meta = clean_redis.hgetall(make_namespace_meta_key("testdomain", "myns"))
        assert "default_cache_mode" in ns_meta

        # Object hash
        obj_data = clean_redis.hgetall(make_object_key("testdomain", "myns", "counter"))
        assert obj_data["type"] == "scalar:int"
        assert obj_data["value"] == "int:42"
        assert obj_data["cache_mode"] == "permanent"
        assert obj_data["ttl"] == ""
        datetime.datetime.fromisoformat(obj_data["created_at"])
        datetime.datetime.fromisoformat(obj_data["updated_at"])


class TestSpecSection12ReadScalarObject:
    """Spec section 12: Reading a scalar object."""

    def test_read_type(self, ns):
        ns["counter"] = 42
        obj = ns.get_object("counter")
        assert obj.type == "scalar:int"

    def test_read_value(self, ns):
        ns["counter"] = 42
        assert ns["counter"] == 42

    def test_read_uri(self, ns):
        ns["counter"] = 42
        obj = ns.get_object("counter")
        assert obj.uri == "gpdc://testdomain/testns/counter"


class TestSpecSection12UpdateScalarObject:
    """Spec section 12: Updating a scalar object."""

    def test_update_via_namespace_setitem(self, ns):
        ns["counter"] = 42
        ns["counter"] = 100
        assert ns["counter"] == 100

    def test_update_via_obj_value_setter(self, ns):
        ns["counter"] = 42
        obj = ns.get_object("counter")
        obj.value = 100
        assert ns["counter"] == 100


class TestSpecSection12DeleteScalarObject:
    """Spec section 12: Deleting a scalar object."""

    def test_delete_removes_key(self, ns, clean_redis):
        ns["counter"] = 42
        del ns["counter"]
        key = make_object_key("testdomain", "testns", "counter")
        assert clean_redis.exists(key) == 0

    def test_delete_makes_contains_false(self, ns):
        ns["counter"] = 42
        del ns["counter"]
        assert "counter" not in ns


class TestSpecSection9CopySemantics:
    """Spec section 9: Scalar value copy semantics."""

    def test_assign_from_object_copies_value(self, ns):
        ns["a"] = 10
        ns["b"] = ns["a"]
        assert ns["b"] == 10

    def test_copies_are_independent(self, ns):
        ns["a"] = 10
        ns["b"] = ns["a"]
        ns["b"] = 20
        assert ns["a"] == 10
        assert ns["b"] == 20

    def test_copy_creates_separate_redis_keys(self, ns, clean_redis):
        ns["a"] = 10
        ns["b"] = ns["a"]
        key_a = make_object_key("testdomain", "testns", "a")
        key_b = make_object_key("testdomain", "testns", "b")
        assert clean_redis.exists(key_a) == 1
        assert clean_redis.exists(key_b) == 1
        assert key_a != key_b


class TestSpecSection12CacheModeExamples:
    """Spec section 12: Cache mode examples."""

    def test_ttl_mode_sets_expiry(self, ns, clean_redis):
        ns["temp"] = 42
        obj = ns.get_object("temp")
        obj.cache_mode = "ttl"
        obj.ttl = 60
        key = make_object_key("testdomain", "testns", "temp")
        remaining = clean_redis.ttl(key)
        assert 55 <= remaining <= 60

    def test_sliding_mode_resets_on_write(self, ns, clean_redis):
        ns["temp"] = 42
        obj = ns.get_object("temp")
        obj.cache_mode = "sliding"
        obj.ttl = 300
        obj.value = 43
        key = make_object_key("testdomain", "testns", "temp")
        remaining = clean_redis.ttl(key)
        assert 295 <= remaining <= 300

    def test_permanent_mode_no_expiry(self, ns, clean_redis):
        ns["perm"] = 42
        obj = ns.get_object("perm")
        obj.cache_mode = "permanent"
        key = make_object_key("testdomain", "testns", "perm")
        assert clean_redis.ttl(key) == -1


class TestNamespaceIteration:
    """Test namespace iteration from spec section 2.3."""

    def test_iterate_all_names(self, ns):
        ns["alpha"] = 1
        ns["beta"] = 2
        ns["gamma"] = 3
        names = sorted(ns)
        assert names == ["alpha", "beta", "gamma"]

    def test_in_operator(self, ns):
        ns["x"] = 1
        assert "x" in ns
        assert "y" not in ns


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
