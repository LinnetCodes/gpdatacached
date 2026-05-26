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
        assert abs(ns["ratio"] - 3.14) < 1e-9

    def test_set_datetime(self, ns):
        dt = datetime.datetime(2024, 6, 15, 9, 0, 0)
        ns["ts"] = dt
        assert ns["ts"] == dt

    def test_set_date(self, ns):
        d = datetime.date(2024, 6, 15)
        ns["day"] = d
        assert ns["day"] == d

    def test_set_time(self, ns):
        t = datetime.time(9, 30, 0)
        ns["t"] = t
        assert ns["t"] == t

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
        assert ns["x"] == 99


class TestNamespaceGetItem:
    def test_get_int_returns_native_value(self, ns):
        ns["n"] = 42
        assert ns["n"] == 42
        assert isinstance(ns["n"], int)

    def test_get_str_returns_native_value(self, ns):
        ns["s"] = "hello"
        assert ns["s"] == "hello"
        assert isinstance(ns["s"], str)

    def test_get_bool_returns_native_value(self, ns):
        ns["b"] = True
        assert ns["b"] is True
        assert isinstance(ns["b"], bool)

    def test_get_missing_key_raises(self, ns):
        with pytest.raises(KeyError):
            _ = ns["nonexistent"]

    def test_value_copy_semantics(self, ns):
        ns["a"] = 10
        ns["b"] = ns["a"]
        assert ns["b"] == 10
        ns["b"] = 20
        assert ns["a"] == 10


class TestGetObject:
    def test_get_object_existing_returns_scalar_object(self, ns):
        from gpdatacached.objects import IntObject

        ns["x"] = 42
        obj = ns.get_object("x")
        assert isinstance(obj, IntObject)
        assert obj.value == 42

    def test_get_object_missing_raises(self, ns):
        with pytest.raises(KeyError):
            ns.get_object("nonexistent")

    def test_get_object_with_native_default_creates_and_returns(self, ns):
        obj = ns.get_object("new_key", 99)
        assert obj.value == 99
        assert "new_key" in ns

    def test_get_object_with_gpdc_object_default_unwraps(self, ns):
        ns["src"] = 10
        obj = ns.get_object("copy", ns.get_object("src"))
        assert obj.value == 10
        assert "copy" in ns

    def test_get_object_existing_ignores_default(self, ns):
        ns["x"] = 42
        obj = ns.get_object("x", 999)
        assert obj.value == 42

    def test_get_object_returns_correct_uri(self, ns):
        ns["counter"] = 1
        obj = ns.get_object("counter")
        assert obj.uri == "gpdc://testdomain/testns/counter"

    def test_get_object_returns_correct_name(self, ns):
        ns["counter"] = 1
        obj = ns.get_object("counter")
        assert obj.name == "counter"


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
        obj = ns.get_object("x")
        obj.value = 99
        assert ns["x"] == 99

    def test_set_value_updates_updated_at(self, ns, clean_redis):
        import time

        ns["x"] = 1
        key = make_object_key("testdomain", "testns", "x")
        first = clean_redis.hget(key, "updated_at")
        time.sleep(0.01)
        obj = ns.get_object("x")
        obj.value = 2
        second = clean_redis.hget(key, "updated_at")
        assert second >= first


class TestCacheModeAndTTL:
    def test_default_cache_mode_is_permanent(self, ns):
        ns["x"] = 1
        obj = ns.get_object("x")
        assert obj.cache_mode == "permanent"

    def test_set_cache_mode_ttl(self, ns):
        ns["x"] = 1
        obj = ns.get_object("x")
        obj.cache_mode = "ttl"
        assert ns.get_object("x").cache_mode == "ttl"

    def test_set_ttl_applies_expire(self, ns, clean_redis):
        ns["x"] = 1
        obj = ns.get_object("x")
        obj.cache_mode = "ttl"
        obj.ttl = 60
        key = make_object_key("testdomain", "testns", "x")
        remaining = clean_redis.ttl(key)
        assert 55 <= remaining <= 60

    def test_persist_removes_ttl(self, ns, clean_redis):
        ns["x"] = 1
        obj = ns.get_object("x")
        obj.cache_mode = "ttl"
        obj.ttl = 60
        key = make_object_key("testdomain", "testns", "x")
        assert clean_redis.hget(key, "ttl") == "60"
        obj.persist()
        assert clean_redis.ttl(key) == -1
        assert clean_redis.hget(key, "ttl") == "60"
        assert clean_redis.hget(key, "cache_mode") == "ttl"

    def test_remaining_ttl_permanent(self, ns):
        ns["x"] = 1
        obj = ns.get_object("x")
        assert obj.remaining_ttl() == -1

    def test_expire_in(self, ns, clean_redis):
        ns["x"] = 1
        obj = ns.get_object("x")
        obj.expire_in(100)
        key = make_object_key("testdomain", "testns", "x")
        remaining = clean_redis.ttl(key)
        assert 95 <= remaining <= 100

    def test_sliding_mode_resets_ttl_on_write(self, ns, clean_redis):
        ns["x"] = 1
        obj = ns.get_object("x")
        obj.cache_mode = "sliding"
        obj.ttl = 300
        obj.value = 2
        key = make_object_key("testdomain", "testns", "x")
        remaining = clean_redis.ttl(key)
        assert 295 <= remaining <= 300

    def test_invalid_cache_mode_raises(self, ns):
        ns["x"] = 1
        obj = ns.get_object("x")
        with pytest.raises(ValueError):
            obj.cache_mode = "invalid"

    def test_scalar_overwrite_preserves_and_reapplies_ttl(self, ns, clean_redis):
        import time

        ns["x"] = 1
        obj = ns.get_object("x")
        obj.cache_mode = "ttl"
        obj.ttl = 300
        key = make_object_key("testdomain", "testns", "x")

        time.sleep(1)
        remaining_before = clean_redis.ttl(key)
        assert remaining_before < 300

        ns["x"] = 42

        data = clean_redis.hgetall(key)
        assert data["cache_mode"] == "ttl"
        assert data["ttl"] == "300"
        remaining = clean_redis.ttl(key)
        assert 290 <= remaining <= 300

    def test_scalar_to_container_overwrite_preserves_and_reapplies_ttl(self, ns, clean_redis):
        import time

        ns["x"] = 1
        obj = ns.get_object("x")
        obj.cache_mode = "ttl"
        obj.ttl = 300
        key = make_object_key("testdomain", "testns", "x")

        time.sleep(1)
        remaining_before = clean_redis.ttl(key)
        assert remaining_before < 300

        ns["x"] = [1, 2, 3]

        data = clean_redis.hgetall(key)
        assert data["type"].startswith("collection:")
        assert data["cache_mode"] == "ttl"
        assert data["ttl"] == "300"
        remaining = clean_redis.ttl(key)
        assert 290 <= remaining <= 300


class TestNamespaceInitWithConfig:
    """GPDCNamespace.__init__ accepts an optional GPDCNamespaceConfig that
    overrides domain defaults for new namespaces."""

    def test_init_with_config_uses_per_namespace_cache_mode(self, domain, clean_redis):
        from gpdatacached.config import GPDCNamespaceConfig
        from gpdatacached.namespace import GPDCNamespace

        cfg = GPDCNamespaceConfig(default_cache_mode="ttl", default_ttl_seconds=1800)
        GPDCNamespace("configured_ns", domain, config=cfg)

        meta = clean_redis.hgetall(make_namespace_meta_key("testdomain", "configured_ns"))
        assert meta["default_cache_mode"] == "ttl"

    def test_init_with_config_uses_per_namespace_ttl(self, domain, clean_redis):
        from gpdatacached.config import GPDCNamespaceConfig
        from gpdatacached.namespace import GPDCNamespace

        cfg = GPDCNamespaceConfig(default_cache_mode="ttl", default_ttl_seconds=1800)
        GPDCNamespace("configured_ns", domain, config=cfg)

        meta = clean_redis.hgetall(make_namespace_meta_key("testdomain", "configured_ns"))
        assert meta["default_ttl_seconds"] == "1800"

    def test_init_without_config_uses_domain_defaults(self, domain, clean_redis):
        from gpdatacached.namespace import GPDCNamespace

        GPDCNamespace("plain_ns", domain)

        meta = clean_redis.hgetall(make_namespace_meta_key("testdomain", "plain_ns"))
        assert meta["default_cache_mode"] == domain.get_default_cache_mode()
        assert meta["default_ttl_seconds"] == str(domain.get_default_ttl_seconds())

    def test_init_prefers_redis_over_passed_config_when_namespace_exists(self, domain, clean_redis):
        """If namespace already exists in Redis, passed-in config must be ignored."""
        from gpdatacached.config import GPDCNamespaceConfig
        from gpdatacached.namespace import GPDCNamespace

        # First creation: uses domain defaults (permanent, 0)
        GPDCNamespace("existing_ns", domain)

        # Second creation with a conflicting config
        cfg = GPDCNamespaceConfig(default_cache_mode="sliding", default_ttl_seconds=9999)
        GPDCNamespace("existing_ns", domain, config=cfg)

        # Redis metadata must still reflect the original (first-creation) values
        meta = clean_redis.hgetall(make_namespace_meta_key("testdomain", "existing_ns"))
        assert meta["default_cache_mode"] == "permanent"
        assert meta["default_ttl_seconds"] == "0"

    def test_init_partial_config_only_cache_mode_falls_back_to_domain_for_ttl(self, domain, clean_redis):
        from gpdatacached.config import GPDCNamespaceConfig
        from gpdatacached.namespace import GPDCNamespace

        # Only cache_mode specified; default_ttl_seconds left as None
        cfg = GPDCNamespaceConfig(default_cache_mode="sliding")
        GPDCNamespace("partial_ns", domain, config=cfg)

        meta = clean_redis.hgetall(make_namespace_meta_key("testdomain", "partial_ns"))
        assert meta["default_cache_mode"] == "sliding"
        assert meta["default_ttl_seconds"] == str(domain.get_default_ttl_seconds())


class TestNamespaceMutableMapping:
    """GPDCNamespace 作为 MutableMapping 子类的完整接口测试。"""

    def test_is_mutablemapping(self, ns):
        from collections.abc import MutableMapping

        assert isinstance(ns, MutableMapping)

    # ── __len__ ─────────────────────────────────────────────────────────────

    def test_len_empty(self, ns):
        assert len(ns) == 0

    def test_len_after_set(self, ns):
        ns["a"] = 1
        ns["b"] = 2
        ns["c"] = 3
        assert len(ns) == 3

    def test_len_after_delete(self, ns):
        ns["a"] = 1
        ns["b"] = 2
        del ns["a"]
        assert len(ns) == 1

    def test_len_excludes_anonymous(self, ns, clean_redis):
        """直接写入匿名对象 Redis key，验证 __len__ 不计入。"""
        import datetime
        from gpdatacached.keys import make_object_key_prefix

        prefix = make_object_key_prefix("testdomain", "testns")
        anon_key = prefix + "~anon:fake-uuid-0001"
        now = datetime.datetime.now(datetime.UTC).isoformat()
        clean_redis.hset(
            anon_key,
            mapping={
                "type": "scalar:int",
                "codec": "native",
                "value": "int:99",
                "cache_mode": "permanent",
                "ttl": "",
                "created_at": now,
                "updated_at": now,
            },
        )
        ns["named_obj"] = 42
        assert len(ns) == 1  # 匿名对象不计入

    # ── __iter__ 匿名过滤 ────────────────────────────────────────────────────

    def test_iter_excludes_anonymous(self, ns, clean_redis):
        """直接写入匿名对象 Redis key，验证 __iter__ 不 yield 该名称。"""
        import datetime
        from gpdatacached.keys import make_object_key_prefix

        prefix = make_object_key_prefix("testdomain", "testns")
        anon_key = prefix + "~anon:fake-uuid-0002"
        now = datetime.datetime.now(datetime.UTC).isoformat()
        clean_redis.hset(
            anon_key,
            mapping={
                "type": "scalar:int",
                "codec": "native",
                "value": "int:7",
                "cache_mode": "permanent",
                "ttl": "",
                "created_at": now,
                "updated_at": now,
            },
        )
        ns["real"] = 1
        assert list(ns) == ["real"]

    # ── MutableMapping mixin 方法 ────────────────────────────────────────────

    def test_keys(self, ns):
        ns["x"] = 1
        ns["y"] = 2
        assert set(ns.keys()) == {"x", "y"}

    def test_values_are_native_values(self, ns):
        ns["x"] = 10
        vals = list(ns.values())
        assert len(vals) == 1
        assert vals[0] == 10

    def test_items(self, ns):
        ns["a"] = 1
        ns["b"] = 2
        items = dict(ns.items())
        assert set(items.keys()) == {"a", "b"}
        assert items["a"] == 1
        assert items["b"] == 2

    def test_get_existing(self, ns):
        ns["x"] = 42
        result = ns.get("x")
        assert result == 42

    def test_get_missing_returns_none(self, ns):
        assert ns.get("nonexistent") is None

    def test_get_missing_custom_default(self, ns):
        sentinel = object()
        assert ns.get("nonexistent", sentinel) is sentinel

    def test_pop_existing(self, ns):
        ns["x"] = 55
        result = ns.pop("x")
        assert result == 55
        assert "x" not in ns

    def test_pop_missing_with_default(self, ns):
        result = ns.pop("nonexistent", None)
        assert result is None

    def test_pop_missing_no_default_raises(self, ns):
        import pytest

        with pytest.raises(KeyError):
            ns.pop("nonexistent")

    def test_update_from_dict(self, ns):
        ns.update({"a": 1, "b": 2})
        assert ns["a"] == 1
        assert ns["b"] == 2
        assert len(ns) == 2

    def test_clear(self, ns):
        ns["a"] = 1
        ns["b"] = 2
        ns.clear()
        assert len(ns) == 0

    def test_clear_also_deletes_anonymous_objects(self, ns, clean_redis):
        """clear() deletes realistic anonymous obj/raw/ref keys created by nested containers."""
        from gpdatacached.keys import make_namespace_meta_key, make_object_key, make_raw_key, make_ref_key

        ns["named"] = {"nested": [1, 2, 3]}
        anon_name = ns["named"]["nested"].name
        anon_obj_key = make_object_key("testdomain", "testns", anon_name)
        anon_raw_key = make_raw_key("testdomain", "testns", anon_name)
        anon_ref_key = make_ref_key("testdomain", "testns", anon_name)

        assert clean_redis.exists(anon_obj_key) == 1
        assert clean_redis.exists(anon_raw_key) == 1
        assert clean_redis.exists(anon_ref_key) == 1

        ns.clear()
        assert len(ns) == 0
        assert clean_redis.exists(anon_obj_key) == 0
        assert clean_redis.exists(anon_raw_key) == 0
        assert clean_redis.exists(anon_ref_key) == 0
        meta_key = make_namespace_meta_key("testdomain", "testns")
        assert clean_redis.exists(meta_key) == 1

    def test_setdefault_missing(self, ns):
        result = ns.setdefault("x", 42)
        assert result == 42
        assert "x" in ns
        assert ns["x"] == 42

    def test_setdefault_existing(self, ns):
        ns["x"] = 10
        result = ns.setdefault("x", 99)
        assert result == 10
