# tests/test_namespace.py
import datetime
import pytest
from gpdatacached.keys import make_namespace_meta_key, make_object_key, make_domain_namespaces_key


class TestNamespaceInit:
    def test_namespace_registers_in_domain(self, ns, clean_redis):
        members = clean_redis.smembers(make_domain_namespaces_key("testdomain"))
        assert "testns" in members

    def test_namespace_meta_written(self, ns, clean_redis):
        meta = clean_redis.hgetall(make_namespace_meta_key("testdomain", "testns"))
        assert "default_cache_mode" in meta

    def test_namespace_name(self, ns):
        assert ns.name == "testns"

    def test_namespace_domain(self, ns, domain):
        assert ns.domain is domain


class TestNamespaceSetItem:
    def test_set_int(self, ns, clean_redis):
        ns["counter"] = 42
        key = make_object_key("testdomain", "testns", "counter")
        data = clean_redis.hgetall(key)
        assert data["type"] == "scalar:int"
        assert data["value"] == "int:42"
        assert data["codec"] == "native"
        assert data["cache_mode"] == "permanent"

    def test_set_str(self, ns, clean_redis):
        ns["msg"] = "hello"
        key = make_object_key("testdomain", "testns", "msg")
        data = clean_redis.hgetall(key)
        assert data["type"] == "scalar:str"
        assert data["value"] == "str:hello"

    def test_set_bool(self, ns, clean_redis):
        ns["flag"] = True
        key = make_object_key("testdomain", "testns", "flag")
        data = clean_redis.hgetall(key)
        assert data["type"] == "scalar:bool"
        assert data["value"] == "bool:True"

    def test_set_none(self, ns, clean_redis):
        ns["nothing"] = None
        data = clean_redis.hgetall(make_object_key("testdomain", "testns", "nothing"))
        assert data["type"] == "scalar:none"

    def test_set_float(self, ns):
        ns["ratio"] = 3.14
        obj = ns["ratio"]
        assert abs(obj.value - 3.14) < 1e-9

    def test_set_datetime(self, ns):
        dt = datetime.datetime(2024, 6, 15, 9, 0, 0)
        ns["ts"] = dt
        assert ns["ts"].value == dt

    def test_set_date(self, ns):
        d = datetime.date(2024, 6, 15)
        ns["day"] = d
        assert ns["day"].value == d

    def test_set_time(self, ns):
        t = datetime.time(9, 30, 0)
        ns["t"] = t
        assert ns["t"].value == t

    def test_set_invalid_name_raises(self, ns):
        with pytest.raises(ValueError):
            ns["my:key"] = 42

    def test_set_reserved_name_raises(self, ns):
        with pytest.raises(ValueError):
            ns["__meta__"] = 42

    def test_set_creates_timestamps(self, ns, clean_redis):
        ns["x"] = 1
        data = clean_redis.hgetall(make_object_key("testdomain", "testns", "x"))
        assert "created_at" in data
        assert "updated_at" in data
        # Both timestamps should be parseable
        datetime.datetime.fromisoformat(data["created_at"])
        datetime.datetime.fromisoformat(data["updated_at"])

    def test_update_preserves_created_at(self, ns, clean_redis):
        ns["x"] = 1
        key = make_object_key("testdomain", "testns", "x")
        created_at = clean_redis.hget(key, "created_at")
        ns["x"] = 2
        assert clean_redis.hget(key, "created_at") == created_at

    def test_update_changes_updated_at(self, ns, clean_redis):
        import time
        ns["x"] = 1
        key = make_object_key("testdomain", "testns", "x")
        updated1 = clean_redis.hget(key, "updated_at")
        time.sleep(0.01)
        ns["x"] = 2
        updated2 = clean_redis.hget(key, "updated_at")
        assert updated2 >= updated1

    def test_update_changes_value(self, ns):
        ns["x"] = 10
        ns["x"] = 99
        assert ns["x"].value == 99


class TestNamespaceGetItem:
    def test_get_int_returns_int_object(self, ns):
        from gpdatacached.objects import IntObject
        ns["n"] = 42
        obj = ns["n"]
        assert isinstance(obj, IntObject)
        assert obj.value == 42

    def test_get_str_returns_str_object(self, ns):
        from gpdatacached.objects import StrObject
        ns["s"] = "hello"
        obj = ns["s"]
        assert isinstance(obj, StrObject)
        assert obj.value == "hello"

    def test_get_bool_returns_bool_object(self, ns):
        from gpdatacached.objects import BoolObject
        ns["b"] = True
        obj = ns["b"]
        assert isinstance(obj, BoolObject)
        assert obj.value is True

    def test_get_missing_key_raises(self, ns):
        with pytest.raises(KeyError):
            _ = ns["nonexistent"]

    def test_get_returns_correct_uri(self, ns):
        ns["counter"] = 1
        obj = ns["counter"]
        assert obj.uri == "gpdc://testdomain/testns/counter"

    def test_get_returns_correct_name(self, ns):
        ns["counter"] = 1
        obj = ns["counter"]
        assert obj.name == "counter"

    def test_value_copy_semantics(self, ns):
        ns["a"] = 10
        ns["b"] = ns["a"]  # ns["a"] is an IntObject; its Python value is copied
        assert ns["b"].value == 10
        ns["b"] = 20
        assert ns["a"].value == 10  # a is unaffected


class TestNamespaceDelItem:
    def test_delete_existing(self, ns, clean_redis):
        ns["x"] = 1
        del ns["x"]
        key = make_object_key("testdomain", "testns", "x")
        assert clean_redis.exists(key) == 0

    def test_delete_missing_raises(self, ns):
        with pytest.raises(KeyError):
            del ns["nonexistent"]


class TestNamespaceContains:
    def test_contains_true(self, ns):
        ns["x"] = 1
        assert "x" in ns

    def test_contains_false(self, ns):
        assert "y" not in ns

    def test_contains_after_delete(self, ns):
        ns["x"] = 1
        del ns["x"]
        assert "x" not in ns


class TestNamespaceIter:
    def test_iter_empty(self, ns):
        assert list(ns) == []

    def test_iter_single(self, ns):
        ns["x"] = 1
        assert list(ns) == ["x"]

    def test_iter_multiple(self, ns):
        ns["a"] = 1
        ns["b"] = 2
        ns["c"] = 3
        assert sorted(ns) == ["a", "b", "c"]


class TestObjectValueSetter:
    def test_set_value_via_obj(self, ns):
        ns["x"] = 10
        obj = ns["x"]
        obj.value = 99
        # Re-read from Redis
        assert ns["x"].value == 99

    def test_set_value_updates_updated_at(self, ns, clean_redis):
        import time
        ns["x"] = 1
        key = make_object_key("testdomain", "testns", "x")
        first = clean_redis.hget(key, "updated_at")
        time.sleep(0.01)
        obj = ns["x"]
        obj.value = 2
        second = clean_redis.hget(key, "updated_at")
        assert second >= first


class TestCacheModeAndTTL:
    def test_default_cache_mode_is_permanent(self, ns):
        ns["x"] = 1
        obj = ns["x"]
        assert obj.cache_mode == "permanent"

    def test_set_cache_mode_ttl(self, ns):
        ns["x"] = 1
        obj = ns["x"]
        obj.cache_mode = "ttl"
        assert ns["x"].cache_mode == "ttl"

    def test_set_ttl_applies_expire(self, ns, clean_redis):
        ns["x"] = 1
        obj = ns["x"]
        obj.cache_mode = "ttl"
        obj.ttl = 60
        key = make_object_key("testdomain", "testns", "x")
        remaining = clean_redis.ttl(key)
        assert 55 <= remaining <= 60

    def test_persist_removes_ttl(self, ns, clean_redis):
        ns["x"] = 1
        obj = ns["x"]
        obj.cache_mode = "ttl"
        obj.ttl = 60
        obj.persist()
        key = make_object_key("testdomain", "testns", "x")
        assert clean_redis.ttl(key) == -1  # permanent

    def test_remaining_ttl_permanent(self, ns):
        ns["x"] = 1
        obj = ns["x"]
        assert obj.remaining_ttl() == -1

    def test_expire_in(self, ns, clean_redis):
        ns["x"] = 1
        obj = ns["x"]
        obj.expire_in(100)
        key = make_object_key("testdomain", "testns", "x")
        remaining = clean_redis.ttl(key)
        assert 95 <= remaining <= 100

    def test_sliding_mode_resets_ttl_on_write(self, ns, clean_redis):
        ns["x"] = 1
        obj = ns["x"]
        obj.cache_mode = "sliding"
        obj.ttl = 300
        # Write new value → TTL should be reset
        obj.value = 2
        key = make_object_key("testdomain", "testns", "x")
        remaining = clean_redis.ttl(key)
        assert 295 <= remaining <= 300

    def test_invalid_cache_mode_raises(self, ns):
        ns["x"] = 1
        obj = ns["x"]
        with pytest.raises(ValueError):
            obj.cache_mode = "invalid"
