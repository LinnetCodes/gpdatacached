import datetime
import threading
import time

import pytest
from gpdatacached import DictObject, ListObject, SetObject
from gpdatacached.exceptions import CanonicalObjectReferencedError
from gpdatacached.keys import (
    make_domain_namespaces_key,
    make_namespace_meta_key,
    make_object_key,
    make_object_key_prefix,
    make_raw_key,
    make_ref_key,
)


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
        datetime.datetime.fromisoformat(data["created_at"])
        datetime.datetime.fromisoformat(data["updated_at"])

    def test_update_preserves_created_at(self, ns, clean_redis):
        ns["x"] = 1
        key = make_object_key("testdomain", "testns", "x")
        created_at = clean_redis.hget(key, "created_at")
        ns["x"] = 2
        assert clean_redis.hget(key, "created_at") == created_at

    def test_update_changes_updated_at(self, ns, clean_redis):
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
        from gpdatacached import IntObject

        ns["x"] = 42
        obj = ns.get_object("x")
        assert isinstance(obj, IntObject)
        assert obj.value == 42

    def test_get_object_missing_raises(self, ns):
        with pytest.raises(KeyError):
            ns.get_object("nonexistent")

    def test_get_object_is_read_only(self, ns):
        assert "z" not in ns
        with pytest.raises(KeyError):
            ns.get_object("z")
        assert "z" not in ns

    def test_get_object_returns_correct_uri(self, ns):
        ns["counter"] = 1
        obj = ns.get_object("counter")
        assert obj.uri == "gpdc://testdomain/testns/counter"

    def test_get_object_returns_correct_name(self, ns):
        ns["counter"] = 1
        obj = ns.get_object("counter")
        assert obj.name == "counter"


class TestEnsureObject:
    def test_ensure_object_creates_when_missing(self, ns):
        obj = ns.ensure_object("new_key", 99)
        assert obj.value == 99
        assert "new_key" in ns

    def test_ensure_object_returns_existing(self, ns):
        ns["x"] = 42
        obj = ns.ensure_object("x", 999)
        assert obj.value == 42

    def test_ensure_object_with_gpdc_object_default(self, ns):
        ns["src"] = 10
        obj = ns.ensure_object("copy", ns.get_object("src"))
        assert obj.value == 10
        assert "copy" in ns

    def test_ensure_object_missing_no_default_raises(self, ns):
        with pytest.raises(TypeError):
            ns.ensure_object("nonexistent")


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
        obj.update_cache_mode_and_ttl("ttl", 60)
        assert ns.get_object("x").cache_mode == "ttl"

    def test_set_ttl_applies_expire(self, ns, clean_redis):
        ns["x"] = 1
        obj = ns.get_object("x")
        obj.update_cache_mode_and_ttl("ttl", 60)
        key = make_object_key("testdomain", "testns", "x")
        remaining = clean_redis.ttl(key)
        assert 55 <= remaining <= 60

    def test_persist_removes_ttl(self, ns, clean_redis):
        ns["x"] = 1
        obj = ns.get_object("x")
        obj.update_cache_mode_and_ttl("ttl", 60)
        key = make_object_key("testdomain", "testns", "x")
        assert clean_redis.hget(key, "ttl") == "60"
        obj.persist()
        assert clean_redis.ttl(key) == -1
        assert clean_redis.hget(key, "cache_mode") == "permanent"
        assert clean_redis.hget(key, "ttl") == ""

    def test_remaining_ttl_permanent(self, ns):
        ns["x"] = 1
        obj = ns.get_object("x")
        assert obj.remaining_ttl() == -1

    def test_expire_in(self, ns, clean_redis):
        ns["x"] = 1
        obj = ns.get_object("x")
        obj.update_cache_mode_and_ttl("ttl", 200)
        obj.expire_in(100)
        key = make_object_key("testdomain", "testns", "x")
        remaining = clean_redis.ttl(key)
        assert 95 <= remaining <= 100

    def test_expire_at_past_time_immediate_expiry(self, ns, clean_redis):
        ns["x"] = 42
        obj = ns.get_object("x")
        obj.update_cache_mode_and_ttl("ttl", 200)
        past = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=100)
        obj.expire_at(past)
        time.sleep(0.1)
        with pytest.raises(KeyError):
            _ = ns["x"]

    def test_expire_at_past_time_deletes_container_fully(self, ns, clean_redis):
        # expire_at(past) must do a full teardown on a container owner, not just
        # delete the obj: key (which would leak raw:/ref:). This mirrors __delitem__.
        ns["c"] = [1, 2, 3]
        obj = ns.get_object("c")
        obj.update_cache_mode_and_ttl("ttl", 200)
        obj_key = make_object_key("testdomain", "testns", "c")
        raw_key = make_raw_key("testdomain", "testns", "c")
        ref_key = make_ref_key("testdomain", "testns", "c")
        assert clean_redis.exists(obj_key, raw_key)

        past = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=100)
        ns.get_object("c").expire_at(past)

        assert not clean_redis.exists(obj_key)
        assert not clean_redis.exists(raw_key)
        assert not clean_redis.exists(ref_key)
        with pytest.raises(KeyError):
            _ = ns["c"]

    def test_expire_at_past_time_cascades_anonymous_subtree(self, ns, clean_redis):
        # A nested container owns anonymous child objects; expire_at(past) on the
        # owner must cascade-delete them, not leave them orphaned.
        ns["nested"] = {"a": [1, 2], "b": [3, 4]}
        obj = ns.get_object("nested")
        obj.update_cache_mode_and_ttl("ttl", 200)
        anon_before = [k for k in clean_redis.scan_iter("gpdc:testdomain:testns:obj:~anon:*")]
        assert len(anon_before) >= 2

        past = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=100)
        ns.get_object("nested").expire_at(past)

        anon_after = [k for k in clean_redis.scan_iter("gpdc:testdomain:testns:obj:~anon:*")]
        assert anon_after == []

    def test_expire_at_past_time_referenced_owner_raises(self, ns, clean_redis):
        # Direction A: expire_at(past) reuses __delitem__, so a canonical owner
        # still referenced by an alias raises CanonicalObjectReferencedError —
        # the caller is responsible for resolving references before expiry.
        ns["base"] = [1, 2, 3]
        ns["base"].update_cache_mode_and_ttl("ttl", 200)
        ns["alias"] = ns["base"]  # alias -> reference binding to canonical "base"

        past = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=100)
        with pytest.raises(CanonicalObjectReferencedError):
            ns.get_object("base").expire_at(past)
        # Nothing was deleted because the expiry was blocked.
        assert clean_redis.exists(make_object_key("testdomain", "testns", "base"))

    def test_container_has_referrers_helper(self, ns, clean_redis):
        # has_referrers() lets users check whether references would block a
        # delete/expire of this owner before attempting it.
        ns["owner"] = [1, 2, 3]
        owner = ns["owner"]
        assert owner.has_referrers() is False

        ns["alias"] = ns["owner"]  # one reference
        assert owner.has_referrers() is True

        del ns["alias"]
        assert owner.has_referrers() is False

    def test_sliding_mode_resets_ttl_on_write(self, ns, clean_redis):
        ns["x"] = 1
        obj = ns.get_object("x")
        obj.update_cache_mode_and_ttl("sliding", 300)
        obj.value = 2
        key = make_object_key("testdomain", "testns", "x")
        remaining = clean_redis.ttl(key)
        assert 295 <= remaining <= 300

    def test_invalid_cache_mode_raises(self, ns):
        ns["x"] = 1
        obj = ns.get_object("x")
        with pytest.raises(ValueError):
            obj.update_cache_mode_and_ttl("invalid", 0)

    def test_scalar_overwrite_preserves_and_reapplies_ttl(self, ns, clean_redis):
        ns["x"] = 1
        obj = ns.get_object("x")
        obj.update_cache_mode_and_ttl("ttl", 300)
        key = make_object_key("testdomain", "testns", "x")

        time.sleep(2)
        remaining_before = clean_redis.ttl(key)
        assert remaining_before < 300

        ns["x"] = 42

        data = clean_redis.hgetall(key)
        assert data["cache_mode"] == "ttl"
        assert data["ttl"] == "300"
        remaining = clean_redis.ttl(key)
        assert 290 <= remaining <= 300

    def test_scalar_to_container_overwrite_resets_policy_to_default(self, ns, clean_redis):
        # Design intent (per docs): only scalar-overwrites-scalar preserves policy.
        # Overwriting a scalar with a container must tear down and reset to namespace
        # defaults (permanent / ttl 0), exactly like a container->container overwrite.
        ns["x"] = 1
        obj = ns.get_object("x")
        obj.update_cache_mode_and_ttl("ttl", 300)
        key = make_object_key("testdomain", "testns", "x")

        ns["x"] = [1, 2, 3]

        data = clean_redis.hgetall(key)
        assert data["type"].startswith("collection:")
        # Policy reset to namespace defaults, not the inherited ttl/300.
        assert data["cache_mode"] == "permanent"
        assert data["ttl"] in ("", "0")
        assert clean_redis.ttl(key) == -1  # permanent → no expiry


class TestNamespaceInitWithConfig:
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
        from gpdatacached.config import GPDCNamespaceConfig
        from gpdatacached.namespace import GPDCNamespace

        GPDCNamespace("existing_ns", domain)

        cfg = GPDCNamespaceConfig(default_cache_mode="sliding", default_ttl_seconds=9999)
        GPDCNamespace("existing_ns", domain, config=cfg)

        meta = clean_redis.hgetall(make_namespace_meta_key("testdomain", "existing_ns"))
        assert meta["default_cache_mode"] == "permanent"
        assert meta["default_ttl_seconds"] == "0"

    def test_init_partial_config_only_cache_mode_falls_back_to_domain_for_ttl(self, domain, clean_redis):
        from gpdatacached.config import GPDCNamespaceConfig
        from gpdatacached.namespace import GPDCNamespace

        cfg = GPDCNamespaceConfig(default_cache_mode="sliding")
        GPDCNamespace("partial_ns", domain, config=cfg)

        meta = clean_redis.hgetall(make_namespace_meta_key("testdomain", "partial_ns"))
        assert meta["default_cache_mode"] == "sliding"
        assert meta["default_ttl_seconds"] == str(domain.get_default_ttl_seconds())


class TestNamespaceMutableMapping:
    def test_is_mutablemapping(self, ns):
        from collections.abc import MutableMapping

        assert isinstance(ns, MutableMapping)

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
        prefix = make_object_key_prefix("testdomain", "testns")
        anon_key = prefix + "~anon:fake-uuid-0001"
        now = datetime.datetime.now(datetime.UTC).isoformat()
        clean_redis.hset(
            anon_key,
            mapping={
                "type": "scalar:int",
                "value": "int:99",
                "cache_mode": "permanent",
                "ttl": "",
                "created_at": now,
                "updated_at": now,
            },
        )
        ns["named_obj"] = 42
        assert len(ns) == 1

    def test_iter_excludes_anonymous(self, ns, clean_redis):
        prefix = make_object_key_prefix("testdomain", "testns")
        anon_key = prefix + "~anon:fake-uuid-0002"
        now = datetime.datetime.now(datetime.UTC).isoformat()
        clean_redis.hset(
            anon_key,
            mapping={
                "type": "scalar:int",
                "value": "int:7",
                "cache_mode": "permanent",
                "ttl": "",
                "created_at": now,
                "updated_at": now,
            },
        )
        ns["real"] = 1
        assert list(ns) == ["real"]

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

    def test_popitem_empty_raises(self, ns):
        with pytest.raises(KeyError):
            ns.popitem()

    def test_popitem_single_item(self, ns):
        ns["x"] = 42
        key, value = ns.popitem()
        assert key == "x"
        assert value == 42
        assert len(ns) == 0

    def test_popitem_reduces_len(self, ns):
        ns["a"] = 1
        ns["b"] = 2
        ns["c"] = 3
        ns.popitem()
        assert len(ns) == 2


class TestNamespaceLargeScale:
    N = 200

    def test_iter_returns_all_keys_with_timing(self, ns):
        for i in range(self.N):
            ns[f"key_{i:04d}"] = i

        expected = [f"key_{i:04d}" for i in range(self.N)]

        start = time.perf_counter()
        result = sorted(ns)
        elapsed = time.perf_counter() - start

        assert result == expected
        print(f"__iter__ {self.N} objects: {elapsed:.3f}s")

    def test_len_returns_correct_count_with_timing(self, ns):
        for i in range(self.N):
            ns[f"key_{i:04d}"] = i

        start = time.perf_counter()
        count = len(ns)
        elapsed = time.perf_counter() - start

        assert count == self.N
        print(f"__len__ {self.N} objects: {elapsed:.3f}s")

    def test_iter_excludes_anonymous_in_large_set(self, ns, clean_redis):
        for i in range(self.N):
            ns[f"key_{i:04d}"] = i

        clean_redis.hset(
            make_object_key("testdomain", "testns", "~ghost"),
            "type",
            "int",
        )
        clean_redis.set(make_raw_key("testdomain", "testns", "~ghost"), "42")

        result = sorted(ns)
        assert len(result) == self.N
        assert "~ghost" not in result


class TestNamespaceSetItemUnique:
    def test_setitem_scalar_object(self, ns):
        ns["x"] = 42
        obj = ns.get_object("x")
        ns["y"] = obj
        assert ns["y"] == 42

    def test_setitem_referrer_can_rebind_to_existing_container_object(self, ns):
        ns["x"] = [1, 2, 3]
        ns["y"] = ns["x"]
        ns["z"] = {"a": 1}

        ns["y"] = ns["z"]

        assert ns["y"]["a"] == 1
        assert list(ns["x"]) == [1, 2, 3]

    def test_contains_with_tilde_name(self, ns):
        assert "~anon:abc" not in ns

    def test_contains_with_empty_string(self, ns):
        assert "" not in ns

    def test_contains_with_non_string(self, ns):
        assert 42 not in ns


class TestListCreation:
    def test_setitem_list_creates_obj_key(self, ns, clean_redis):
        ns["mylist"] = [1, 2, 3]
        obj_key = make_object_key("testdomain", "testns", "mylist")
        assert clean_redis.exists(obj_key)
        assert clean_redis.hget(obj_key, "type") == "collection:list"

    def test_setitem_list_creates_raw_key(self, ns, clean_redis):
        ns["mylist"] = [1, 2, 3]
        raw_key = make_raw_key("testdomain", "testns", "mylist")
        assert clean_redis.exists(raw_key)
        assert clean_redis.llen(raw_key) == 3

    def test_getitem_list_returns_list_object(self, ns):
        ns["mylist"] = [1, 2, 3]
        result = ns["mylist"]
        assert isinstance(result, ListObject)

    def test_getitem_list_index(self, ns):
        ns["mylist"] = [10, 20, 30]
        lst = ns["mylist"]
        assert lst[0] == 10
        assert lst[1] == 20
        assert lst[2] == 30

    def test_list_append(self, ns):
        ns["mylist"] = [1, 2, 3]
        lst = ns["mylist"]
        lst.append(4)
        assert list(ns["mylist"]) == [1, 2, 3, 4]

    def test_new_list_has_empty_referrers(self, ns):
        ns["mylist"] = [1, 2, 3]
        obj = ns["mylist"]
        assert obj.referrers() == set()


class TestListReferenceSharing:
    def test_assign_list_object_shares_raw_key(self, ns, clean_redis):
        ns["a"] = [1, 2, 3]
        ns["b"] = ns["a"]
        obj_a_key = make_object_key("testdomain", "testns", "a")
        obj_b_key = make_object_key("testdomain", "testns", "b")
        assert clean_redis.type(obj_b_key) == "string"
        assert clean_redis.get(obj_b_key) == f"ref:{obj_a_key}"

    def test_assign_list_object_adds_to_ref_set(self, ns, clean_redis):
        ns["a"] = [1, 2, 3]
        ns["b"] = ns["a"]
        ref_key = make_ref_key("testdomain", "testns", "a")
        assert clean_redis.hexists(ref_key, "b")

    def test_referrers_method(self, ns):
        ns["a"] = [1, 2, 3]
        ns["b"] = ns["a"]
        assert "b" in ns["a"].referrers()

    def test_shared_modification_visible_through_both(self, ns):
        ns["a"] = [1, 2, 3]
        ns["b"] = ns["a"]
        ns["b"].append(4)
        assert list(ns["a"]) == [1, 2, 3, 4]

    def test_no_raw_key_for_reference(self, ns, clean_redis):
        ns["a"] = [1, 2, 3]
        ns["b"] = ns["a"]
        raw_b = make_raw_key("testdomain", "testns", "b")
        obj_b_key = make_object_key("testdomain", "testns", "b")
        assert clean_redis.type(obj_b_key) == "string"
        assert clean_redis.get(obj_b_key).startswith("ref:")
        assert not clean_redis.exists(raw_b)


class TestListCopySemantics:
    def test_copy_creates_independent_list(self, ns):
        ns["a"] = [1, 2, 3]
        ns["c"] = ns["a"].copy()
        ns["c"].append(4)
        assert list(ns["a"]) == [1, 2, 3]
        assert list(ns["c"]) == [1, 2, 3, 4]

    def test_copy_does_not_add_to_ref_set(self, ns, clean_redis):
        ns["a"] = [1, 2, 3]
        ns["c"] = ns["a"].copy()
        ref_key = make_ref_key("testdomain", "testns", "a")
        assert not clean_redis.hexists(ref_key, "c")

    def test_copy_creates_separate_raw_key(self, ns, clean_redis):
        ns["a"] = [1, 2, 3]
        ns["c"] = ns["a"].copy()
        raw_a = make_raw_key("testdomain", "testns", "a")
        raw_c = make_raw_key("testdomain", "testns", "c")
        obj_c_value = clean_redis.hget(make_object_key("testdomain", "testns", "c"), "value")
        assert obj_c_value == raw_c
        assert obj_c_value != raw_a


class TestListReassignment:
    def test_reassign_removes_from_old_ref_set(self, ns, clean_redis):
        ns["x"] = [1, 2, 3]
        ns["y"] = ns["x"]
        ref_x = make_ref_key("testdomain", "testns", "x")
        assert clean_redis.hexists(ref_x, "y")
        ns["y"] = [4, 5, 6]
        assert not clean_redis.hexists(ref_x, "y")

    def test_reassign_creates_new_raw_key(self, ns, clean_redis):
        ns["x"] = [1, 2, 3]
        ns["y"] = ns["x"]
        ns["y"] = [4, 5, 6]
        raw_y = make_raw_key("testdomain", "testns", "y")
        assert clean_redis.exists(raw_y)
        assert clean_redis.llen(raw_y) == 3

    def test_reassign_new_list_independent(self, ns):
        ns["x"] = [1, 2, 3]
        ns["y"] = ns["x"]
        ns["y"] = [4, 5, 6]
        assert list(ns["x"]) == [1, 2, 3]
        assert list(ns["y"]) == [4, 5, 6]


class TestListDeletion:
    def test_delete_no_referrers_removes_all_keys(self, ns, clean_redis):
        ns["a"] = [1, 2, 3]
        del ns["a"]
        assert not clean_redis.exists(make_object_key("testdomain", "testns", "a"))
        assert not clean_redis.exists(make_raw_key("testdomain", "testns", "a"))
        assert not clean_redis.exists(make_ref_key("testdomain", "testns", "a"))

    def test_delete_with_referrers_raises_error(self, ns):
        ns["a"] = [1, 2, 3]
        ns["b"] = ns["a"]
        with pytest.raises(CanonicalObjectReferencedError):
            del ns["a"]

    def test_delete_referrer_removes_from_original_ref_set(self, ns, clean_redis):
        ns["a"] = [1, 2, 3]
        ns["b"] = ns["a"]
        del ns["b"]
        assert not clean_redis.exists(make_object_key("testdomain", "testns", "b"))
        ref_a = make_ref_key("testdomain", "testns", "a")
        assert not clean_redis.hexists(ref_a, "b")

    def test_delete_referrer_leaves_raw_data_intact(self, ns, clean_redis):
        ns["a"] = [1, 2, 3]
        ns["b"] = ns["a"]

        raw_key_a = clean_redis.hget(make_object_key("testdomain", "testns", "a"), "value")

        del ns["b"]

        assert clean_redis.exists(raw_key_a) == 1
        assert clean_redis.lrange(raw_key_a, 0, -1) == ["int:1", "int:2", "int:3"]

        lst = ns["a"]
        assert list(lst) == [1, 2, 3]

    def test_delete_nonexistent_raises_key_error(self, ns):
        with pytest.raises(KeyError):
            del ns["nonexistent"]


class TestListMixedTypes:
    def test_scalar_still_works(self, ns):
        ns["x"] = 42
        assert ns["x"] == 42

    def test_scalar_and_list_coexist(self, ns):
        ns["x"] = 42
        ns["mylist"] = [1, 2, 3]
        assert ns["x"] == 42
        assert isinstance(ns["mylist"], ListObject)
        assert list(ns["mylist"]) == [1, 2, 3]


class TestDictCreation:
    def test_setitem_dict_creates_obj_key(self, ns, clean_redis):
        ns["mydict"] = {"a": 1, "b": "hello"}
        obj_key = make_object_key("testdomain", "testns", "mydict")
        assert clean_redis.exists(obj_key)
        assert clean_redis.hget(obj_key, "type") == "collection:dict"

    def test_setitem_dict_creates_raw_key(self, ns, clean_redis):
        ns["mydict"] = {"a": 1, "b": "hello"}
        raw_key = make_raw_key("testdomain", "testns", "mydict")
        assert clean_redis.exists(raw_key)
        assert clean_redis.hlen(raw_key) == 2

    def test_getitem_dict_returns_dict_object(self, ns):
        ns["mydict"] = {"a": 1, "b": "hello"}
        result = ns["mydict"]
        assert isinstance(result, DictObject)

    def test_dict_read_write(self, ns):
        ns["mydict"] = {"name": "Alice", "age": 30}
        d = ns["mydict"]
        assert d["name"] == "Alice"
        assert d["age"] == 30

    def test_new_dict_has_empty_referrers(self, ns):
        ns["mydict"] = {"a": 1}
        obj = ns["mydict"]
        assert obj.referrers() == set()


class TestDictReferenceSharing:
    def test_assign_dict_object_shares_raw_key(self, ns, clean_redis):
        ns["a"] = {"x": 1}
        ns["b"] = ns["a"]
        obj_a_key = make_object_key("testdomain", "testns", "a")
        obj_b_key = make_object_key("testdomain", "testns", "b")
        assert clean_redis.type(obj_b_key) == "string"
        assert clean_redis.get(obj_b_key) == f"ref:{obj_a_key}"

    def test_assign_dict_object_adds_to_ref_set(self, ns, clean_redis):
        ns["a"] = {"x": 1}
        ns["b"] = ns["a"]
        ref_key = make_ref_key("testdomain", "testns", "a")
        assert clean_redis.hexists(ref_key, "b")

    def test_shared_dict_modification_visible(self, ns):
        ns["a"] = {"x": 1}
        ns["b"] = ns["a"]
        ns["b"]["y"] = 2
        assert ns["a"]["y"] == 2


class TestDictDeletion:
    def test_delete_no_referrers_removes_all_keys(self, ns, clean_redis):
        ns["d"] = {"x": 1}
        del ns["d"]
        assert not clean_redis.exists(make_object_key("testdomain", "testns", "d"))
        assert not clean_redis.exists(make_raw_key("testdomain", "testns", "d"))
        assert not clean_redis.exists(make_ref_key("testdomain", "testns", "d"))

    def test_delete_with_referrers_raises_error(self, ns):
        ns["a"] = {"x": 1}
        ns["b"] = ns["a"]
        with pytest.raises(CanonicalObjectReferencedError):
            del ns["a"]

    def test_delete_nonexistent_raises(self, ns):
        with pytest.raises(KeyError):
            del ns["nonexistent"]


class TestDictMixedTypes:
    def test_scalar_list_dict_coexist(self, ns):
        ns["s"] = 42
        ns["l"] = [1, 2, 3]
        ns["d"] = {"a": 1}
        assert ns["s"] == 42
        d = ns["d"]
        assert isinstance(d, DictObject)
        assert d["a"] == 1


class TestSetCreation:
    def test_assign_set_creates_object(self, ns, clean_redis):
        ns["primes"] = {2, 3, 5, 7}
        obj_key = make_object_key("testdomain", "testns", "primes")
        assert clean_redis.hget(obj_key, "type") == "collection:set"

    def test_assign_set_creates_raw(self, ns, clean_redis):
        ns["primes"] = {2, 3, 5}
        raw_key = make_raw_key("testdomain", "testns", "primes")
        members = clean_redis.smembers(raw_key)
        assert members == {"int:2", "int:3", "int:5"}

    def test_assign_set_returns_setobject(self, ns):
        ns["primes"] = {2, 3, 5}
        obj = ns["primes"]
        assert isinstance(obj, SetObject)

    def test_assign_set_correct_value(self, ns):
        ns["primes"] = {2, 3, 5}
        obj = ns["primes"]
        assert set(obj) == {2, 3, 5}

    def test_assign_empty_set(self, ns, clean_redis):
        ns["empty"] = set()
        obj_key = make_object_key("testdomain", "testns", "empty")
        assert clean_redis.hget(obj_key, "type") == "collection:set"
        obj = ns["empty"]
        assert len(obj) == 0

    def test_assign_set_with_tuples(self, ns):
        ns["tset"] = {1, (2, 3)}
        obj = ns["tset"]
        assert 1 in obj
        assert (2, 3) in obj


class TestSetDeletion:
    def test_del_set_removes_obj_key(self, ns, clean_redis):
        ns["primes"] = {2, 3, 5}
        obj_key = make_object_key("testdomain", "testns", "primes")
        assert clean_redis.exists(obj_key)
        del ns["primes"]
        assert not clean_redis.exists(obj_key)

    def test_del_set_removes_raw_key(self, ns, clean_redis):
        ns["primes"] = {2, 3, 5}
        raw_key = make_raw_key("testdomain", "testns", "primes")
        assert clean_redis.exists(raw_key)
        del ns["primes"]
        assert not clean_redis.exists(raw_key)

    def test_del_set_removes_ref_key(self, ns, clean_redis):
        ns["primes"] = {2, 3, 5}
        ref_key = make_ref_key("testdomain", "testns", "primes")
        del ns["primes"]
        assert not clean_redis.exists(ref_key)


class TestSetReferenceSharing:
    def test_assign_setobject_shares_raw(self, ns, clean_redis):
        ns["a"] = {1, 2, 3}
        ns["b"] = ns["a"]
        obj_a_key = make_object_key("testdomain", "testns", "a")
        obj_b = make_object_key("testdomain", "testns", "b")
        assert clean_redis.type(obj_b) == "string"
        assert clean_redis.get(obj_b) == f"ref:{obj_a_key}"

    def test_ref_set_tracks_referrer(self, ns, clean_redis):
        ns["a"] = {1, 2, 3}
        ns["b"] = ns["a"]
        ref_key_a = make_ref_key("testdomain", "testns", "a")
        assert clean_redis.hexists(ref_key_a, "b")

    def test_del_referrer_preserves_raw(self, ns, clean_redis):
        ns["a"] = {1, 2, 3}
        ns["b"] = ns["a"]
        del ns["b"]
        raw_a = make_raw_key("testdomain", "testns", "a")
        assert clean_redis.exists(raw_a)

    def test_reassign_referrer_cleans_old_ref(self, ns, clean_redis):
        ns["a"] = {1, 2, 3}
        ns["b"] = ns["a"]
        ns["b"] = {4, 5}
        ref_key_a = make_ref_key("testdomain", "testns", "a")
        assert not clean_redis.hexists(ref_key_a, "b")


class TestAliasStorageModel:
    def test_owner_stored_as_hash(self, ns):
        ns["mylist"] = [1, 2, 3]
        r = ns._redis()
        obj_key = make_object_key(ns.domain.name, ns.name, "mylist")
        assert r.type(obj_key) == "hash"

    def test_alias_stored_as_string(self, ns):
        ns["owner"] = [10, 20]
        ns["alias"] = ns["owner"]
        r = ns._redis()
        alias_key = make_object_key(ns.domain.name, ns.name, "alias")
        assert r.type(alias_key) == "string"

    def test_alias_string_value_is_ref_to_canonical(self, ns):
        ns["owner"] = [10, 20]
        ns["alias"] = ns["owner"]
        r = ns._redis()
        alias_key = make_object_key(ns.domain.name, ns.name, "alias")
        owner_key = make_object_key(ns.domain.name, ns.name, "owner")
        assert r.get(alias_key) == f"ref:{owner_key}"

    def test_owner_metadata_has_no_binding_kind(self, ns):
        ns["mylist"] = [1, 2, 3]
        r = ns._redis()
        obj_key = make_object_key(ns.domain.name, ns.name, "mylist")
        meta = r.hgetall(obj_key)
        assert "binding_kind" not in meta

    def test_alias_in_ref_set(self, ns):
        ns["owner"] = [10, 20]
        ns["alias"] = ns["owner"]
        r = ns._redis()
        ref_key = make_ref_key(ns.domain.name, ns.name, "owner")
        assert set(r.hkeys(ref_key)) == {"alias"}


class TestAliasHandleProperties:
    def test_alias_name_is_alias(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        obj = ns.get_object("alias")
        assert obj.name == "alias"

    def test_alias_canonical_name_is_owner(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        obj = ns.get_object("alias")
        assert obj.canonical_name == "owner"

    def test_alias_is_reference_true(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        obj = ns.get_object("alias")
        assert obj.is_reference is True

    def test_owner_is_reference_false(self, ns):
        ns["owner"] = [1, 2]
        obj = ns.get_object("owner")
        assert obj.is_reference is False

    def test_alias_uri_uses_alias_name(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        obj = ns.get_object("alias")
        assert "alias" in obj.uri
        assert "owner" not in obj.uri

    def test_alias_canonical_uri_uses_owner_name(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        obj = ns.get_object("alias")
        assert "owner" in obj.canonical_uri

    def test_alias_reads_same_data_as_owner(self, ns):
        ns["owner"] = [10, 20, 30]
        ns["alias"] = ns["owner"]
        assert ns["alias"].value == [10, 20, 30]

    def test_mutation_through_alias_visible_through_owner(self, ns):
        ns["owner"] = [10, 20]
        ns["alias"] = ns["owner"]
        ns["alias"].append(30)
        assert ns["owner"].value == [10, 20, 30]


class TestAliasLifecycleResolvesToCanonical:
    def test_cache_mode_reads_from_canonical(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        assert ns.get_object("alias").cache_mode == ns.get_object("owner").cache_mode

    def test_cache_mode_set_through_alias_affects_canonical(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        alias_obj = ns.get_object("alias")
        alias_obj.update_cache_mode_and_ttl("ttl", 300)
        assert ns.get_object("owner").cache_mode == "ttl"

    def test_ttl_set_through_alias_affects_canonical(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        alias_obj = ns.get_object("alias")
        alias_obj.update_cache_mode_and_ttl("ttl", 300)
        assert ns.get_object("owner").ttl == 300

    def test_created_at_reads_from_canonical(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        assert ns.get_object("alias").created_at == ns.get_object("owner").created_at

    def test_updated_at_reads_from_canonical(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        assert ns.get_object("alias").updated_at == ns.get_object("owner").updated_at

    def test_remaining_ttl_resolves_to_canonical(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        alias_obj = ns.get_object("alias")
        alias_obj.update_cache_mode_and_ttl("ttl", 300)
        owner_obj = ns.get_object("owner")
        assert alias_obj.remaining_ttl() == owner_obj.remaining_ttl()

    def test_expire_in_through_alias_affects_canonical(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        alias_obj = ns.get_object("alias")
        alias_obj.update_cache_mode_and_ttl("ttl", 300)
        alias_obj.expire_in(600)
        assert ns.get_object("owner").remaining_ttl() > 0

    def test_expire_at_through_alias_affects_canonical(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        alias_obj = ns.get_object("alias")
        alias_obj.update_cache_mode_and_ttl("ttl", 200)
        future = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=600)
        alias_obj.expire_at(future)
        assert ns.get_object("owner").remaining_ttl() > 0

    def test_persist_through_alias_affects_canonical(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        alias_obj = ns.get_object("alias")
        alias_obj.update_cache_mode_and_ttl("ttl", 300)
        alias_obj.persist()
        assert ns.get_object("owner").remaining_ttl() == -1


class TestOwnerDeletionBlocking:
    def test_delete_owner_with_refs_raises_error(self, ns):
        ns["owner"] = [1, 2, 3]
        ns["alias"] = ns["owner"]
        with pytest.raises(CanonicalObjectReferencedError) as exc_info:
            del ns["owner"]
        assert exc_info.value.canonical_name == "owner"
        assert "alias" in exc_info.value.referrers

    def test_delete_owner_without_refs_succeeds(self, ns):
        ns["owner"] = [1, 2, 3]
        del ns["owner"]
        assert "owner" not in ns

    def test_delete_alias_succeeds(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        del ns["alias"]
        assert "alias" not in ns
        assert "owner" in ns

    def test_delete_alias_removes_from_ref_set(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        del ns["alias"]
        r = ns._redis()
        ref_key = make_ref_key(ns.domain.name, ns.name, "owner")
        assert r.hlen(ref_key) == 0

    def test_delete_owner_after_all_aliases_removed(self, ns):
        ns["owner"] = [1, 2]
        ns["a1"] = ns["owner"]
        ns["a2"] = ns["owner"]
        del ns["a1"]
        del ns["a2"]
        del ns["owner"]
        assert "owner" not in ns


class TestOwnerOverwrite:
    def test_overwrite_owner_with_scalar(self, ns):
        ns["x"] = [1, 2, 3]
        ns["x"] = 42
        assert ns["x"] == 42

    def test_overwrite_owner_with_new_container(self, ns):
        ns["x"] = [1, 2, 3]
        ns["x"] = {"a": 1}
        assert ns["x"].value == {"a": 1}

    def test_overwrite_owner_with_refs_raises_error(self, ns):
        ns["x"] = [1, 2, 3]
        ns["y"] = ns["x"]
        with pytest.raises(CanonicalObjectReferencedError):
            ns["x"] = 42

    def test_overwrite_alias_with_scalar(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        ns["alias"] = 99
        assert ns["alias"] == 99
        assert ns["owner"].value == [1, 2]

    def test_overwrite_alias_with_new_container(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        ns["alias"] = {"new": True}
        assert ns["alias"].value == {"new": True}
        assert ns["owner"].value == [1, 2]


class TestSameRefNoOp:
    def test_same_ref_assignment_is_noop(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        ns["alias"] = ns["owner"]
        r = ns._redis()
        ref_key = make_ref_key(ns.domain.name, ns.name, "owner")
        assert set(r.hkeys(ref_key)) == {"alias"}

    def test_self_assignment_of_owner_is_noop(self, ns):
        ns["x"] = [1, 2, 3]
        ns["x"] = ns["x"]
        assert ns["x"].value == [1, 2, 3]


class TestBindingResolution:
    def test_resolve_owner_hash(self, ns):
        ns["mylist"] = [1, 2]
        result = ns._resolve_binding("mylist")
        assert result is not None
        assert result.binding_kind == "owner"
        assert result.canonical_name == "mylist"
        assert result.binding_name == "mylist"

    def test_resolve_alias_string(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        result = ns._resolve_binding("alias")
        assert result is not None
        assert result.binding_kind == "reference"
        assert result.canonical_name == "owner"
        assert result.binding_name == "alias"

    def test_resolve_nonexistent_returns_none(self, ns):
        result = ns._resolve_binding("nosuchkey")
        assert result is None

    def test_resolve_scalar_owner(self, ns):
        ns["x"] = 42
        result = ns._resolve_binding("x")
        assert result is not None
        assert result.binding_kind == "owner"
        assert result.meta.type == "scalar:int"

    def test_resolve_malformed_ref_returns_none(self, ns):
        r = ns._redis()
        alias_key = make_object_key(ns.domain.name, ns.name, "bad_alias")
        raw_key = make_raw_key(ns.domain.name, ns.name, "owner")
        r.set(alias_key, f"ref:{raw_key}")
        result = ns._resolve_binding("bad_alias")
        assert result is None
        r.delete(alias_key)

    def test_resolve_cross_namespace_ref_returns_none(self, ns):
        r = ns._redis()
        alias_key = make_object_key(ns.domain.name, ns.name, "bad_alias")
        cross_ns_obj_key = make_object_key(ns.domain.name, "other_ns", "owner")
        r.set(alias_key, f"ref:{cross_ns_obj_key}")
        result = ns._resolve_binding("bad_alias")
        assert result is None
        r.delete(alias_key)

    def test_resolve_alias_to_alias_returns_none(self, ns):
        r = ns._redis()
        ns["owner"] = [1, 2]
        ns["alias1"] = ns["owner"]
        alias1_key = make_object_key(ns.domain.name, ns.name, "alias1")
        alias2_key = make_object_key(ns.domain.name, ns.name, "alias2")
        r.set(alias2_key, f"ref:{alias1_key}")
        result = ns._resolve_binding("alias2")
        assert result is None
        r.delete(alias2_key)

    def test_resolve_string_binding_to_non_hash_returns_none(self, ns):
        r = ns._redis()
        owner_key = make_object_key(ns.domain.name, ns.name, "owner")
        alias_key = make_object_key(ns.domain.name, ns.name, "bad_alias")
        r.set(owner_key, "not_a_hash_value")
        r.set(alias_key, f"ref:{owner_key}")
        result = ns._resolve_binding("bad_alias")
        assert result is None
        r.delete(alias_key)
        r.delete(owner_key)


class TestContainerThroughAlias:
    def test_list_append_through_alias(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        ns["alias"].append(3)
        assert ns["owner"].value == [1, 2, 3]

    def test_dict_setitem_through_alias(self, ns):
        ns["owner"] = {"a": 1}
        ns["alias"] = ns["owner"]
        ns["alias"]["b"] = 2
        assert ns["owner"].value == {"a": 1, "b": 2}

    def test_set_add_through_alias(self, ns):
        ns["owner"] = {1, 2}
        ns["alias"] = ns["owner"]
        ns["alias"].add(3)
        assert 3 in ns["owner"]

    def test_alias_referrers_returns_canonical_refs(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        owner_obj = ns.get_object("owner")
        alias_obj = ns.get_object("alias")
        assert owner_obj.referrers() == {"alias"}
        assert alias_obj.referrers() == {"alias"}

    def test_alias_touch_updates_canonical_timestamp(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        owner_updated = ns.get_object("owner").updated_at
        ns["alias"].append(99)
        new_updated = ns.get_object("owner").updated_at
        assert new_updated >= owner_updated


class TestStaleAliasContains:
    def test_stale_alias_not_in_ns(self, ns):
        ns["owner"] = [1, 2, 3]
        ns["alias"] = ns["owner"]
        r = ns._redis()
        owner_key = make_object_key(ns.domain.name, ns.name, "owner")
        ref_key = make_ref_key(ns.domain.name, ns.name, "owner")
        r.delete(owner_key)
        r.delete(ref_key)
        assert "alias" not in ns

    def test_valid_alias_in_ns(self, ns):
        ns["owner"] = [1, 2, 3]
        ns["alias"] = ns["owner"]
        assert "alias" in ns

    def test_owner_still_in_ns_after_owner_deleted(self, ns):
        ns["owner"] = [1, 2, 3]
        assert "owner" in ns
        del ns["owner"]
        assert "owner" not in ns

    def test_stale_alias_string_key_still_exists_in_redis(self, ns):
        ns["owner"] = [1, 2, 3]
        ns["alias"] = ns["owner"]
        r = ns._redis()
        owner_key = make_object_key(ns.domain.name, ns.name, "owner")
        ref_key = make_ref_key(ns.domain.name, ns.name, "owner")
        r.delete(owner_key)
        r.delete(ref_key)
        alias_key = make_object_key(ns.domain.name, ns.name, "alias")
        assert r.exists(alias_key) > 0
        assert "alias" not in ns


class TestStaleAliasGetItem:
    def test_stale_alias_getitem_raises_keyerror(self, ns):
        ns["owner"] = [1, 2, 3]
        ns["alias"] = ns["owner"]
        r = ns._redis()
        owner_key = make_object_key(ns.domain.name, ns.name, "owner")
        ref_key = make_ref_key(ns.domain.name, ns.name, "owner")
        r.delete(owner_key)
        r.delete(ref_key)
        with pytest.raises(KeyError):
            ns["alias"]

    def test_valid_alias_getitem_works(self, ns):
        ns["owner"] = [1, 2, 3]
        ns["alias"] = ns["owner"]
        assert ns["alias"].value == [1, 2, 3]


class TestStaleAliasIter:
    def test_iter_excludes_stale_alias(self, ns):
        ns["owner"] = [1, 2, 3]
        ns["alias"] = ns["owner"]
        ns["other"] = "hello"
        r = ns._redis()
        owner_key = make_object_key(ns.domain.name, ns.name, "owner")
        ref_key = make_ref_key(ns.domain.name, ns.name, "owner")
        r.delete(owner_key)
        r.delete(ref_key)
        names = list(ns)
        assert "alias" not in names
        assert "other" in names

    def test_iter_includes_valid_alias(self, ns):
        ns["owner"] = [1, 2, 3]
        ns["alias"] = ns["owner"]
        names = list(ns)
        assert "alias" in names
        assert "owner" in names

    def test_iter_multiple_stale_and_valid(self, ns):
        ns["owner1"] = [1, 2]
        ns["owner2"] = [3, 4]
        ns["alias1"] = ns["owner1"]
        ns["alias2"] = ns["owner2"]
        ns["standalone"] = "val"
        r = ns._redis()
        owner1_key = make_object_key(ns.domain.name, ns.name, "owner1")
        ref1_key = make_ref_key(ns.domain.name, ns.name, "owner1")
        r.delete(owner1_key)
        r.delete(ref1_key)
        names = list(ns)
        assert "alias1" not in names
        assert "owner1" not in names
        assert "alias2" in names
        assert "owner2" in names
        assert "standalone" in names


class TestStaleAliasLen:
    def test_len_excludes_stale_alias(self, ns):
        ns["owner"] = [1, 2, 3]
        ns["alias"] = ns["owner"]
        ns["other"] = "hello"
        r = ns._redis()
        owner_key = make_object_key(ns.domain.name, ns.name, "owner")
        ref_key = make_ref_key(ns.domain.name, ns.name, "owner")
        r.delete(owner_key)
        r.delete(ref_key)
        assert len(ns) == 1
        assert set(ns) == {"other"}

    def test_stale_alias_not_counted_fix(self, ns):
        ns["owner"] = [1, 2, 3]
        ns["alias"] = ns["owner"]
        r = ns._redis()
        owner_key = make_object_key(ns.domain.name, ns.name, "owner")
        ref_key = make_ref_key(ns.domain.name, ns.name, "owner")
        r.delete(owner_key)
        r.delete(ref_key)
        assert len(ns) == 0

    def test_len_counts_valid_alias(self, ns):
        ns["owner"] = [1, 2, 3]
        ns["alias"] = ns["owner"]
        assert len(ns) == 2


class TestReassignmentRefCleanup:
    def test_overwrite_owner_list_with_scalar_when_no_refs(self, ns):
        ns["x"] = [1, 2, 3]
        ns["x"] = 42
        assert ns["x"] == 42

    def test_reassign_dict_to_scalar_cleans_ref(self, ns, clean_redis):
        ns["x"] = {"a": 1}
        ns["y"] = ns["x"]
        ref_x = make_ref_key("testdomain", "testns", "x")
        assert clean_redis.hexists(ref_x, "y")

        ns["y"] = "hello"
        assert not clean_redis.hexists(ref_x, "y")

    def test_reassign_set_to_scalar_cleans_ref(self, ns, clean_redis):
        ns["x"] = {1, 2, 3}
        ns["y"] = ns["x"]
        ref_x = make_ref_key("testdomain", "testns", "x")
        assert clean_redis.hexists(ref_x, "y")

        ns["y"] = 99
        assert not clean_redis.hexists(ref_x, "y")

    def test_reassign_alias_to_new_container_cleans_ref(self, ns, clean_redis):
        ns["a"] = [1, 2]
        ns["b"] = ns["a"]
        ns["c"] = [10, 20]
        ns["b"] = ns["c"]
        ref_a = make_ref_key("testdomain", "testns", "a")
        ref_c = make_ref_key("testdomain", "testns", "c")
        assert not clean_redis.hexists(ref_a, "b")
        assert clean_redis.hexists(ref_c, "b")

    def test_reassign_ref_to_existing_container_cleans_old_ref(self, ns, clean_redis):
        ns["x"] = [1, 2]
        ns["y"] = [3, 4]
        ns["z"] = ns["x"]

        ns["z"] = ns["y"]
        ref_x = make_ref_key("testdomain", "testns", "x")
        ref_y = make_ref_key("testdomain", "testns", "y")
        assert not clean_redis.hexists(ref_x, "z")
        assert clean_redis.hexists(ref_y, "z")


class TestMultipleRefs:
    def test_multiple_refs_to_same_container(self, ns, clean_redis):
        ns["x"] = [1, 2, 3]
        ns["y"] = ns["x"]
        ns["z"] = ns["x"]
        ref_x = make_ref_key("testdomain", "testns", "x")
        assert set(clean_redis.hkeys(ref_x)) == {"y", "z"}

    def test_del_one_ref_preserves_data(self, ns, clean_redis):
        ns["x"] = [1, 2, 3]
        ns["y"] = ns["x"]
        ns["z"] = ns["x"]
        del ns["z"]
        raw_x = make_raw_key("testdomain", "testns", "x")
        assert clean_redis.exists(raw_x)

    def test_del_all_refs_preserves_owner(self, ns, clean_redis):
        ns["x"] = [1, 2, 3]
        ns["y"] = ns["x"]
        del ns["y"]
        raw_x = make_raw_key("testdomain", "testns", "x")
        obj_x = make_object_key("testdomain", "testns", "x")
        assert clean_redis.exists(raw_x)
        assert clean_redis.exists(obj_x)

    def test_delete_owner_blocked_when_refs_exist(self, ns):
        ns["x"] = [1, 2]
        ns["y"] = ns["x"]
        with pytest.raises(CanonicalObjectReferencedError):
            del ns["x"]
        assert ns["x"].value == [1, 2]
        assert ns["y"].value == [1, 2]


class TestReassignToDifferentContainerType:
    def test_reassign_list_to_dict(self, ns, clean_redis):
        ns["x"] = [1, 2, 3]
        ns["y"] = ns["x"]
        ns["y"] = {"a": 1}
        ref_x = make_ref_key("testdomain", "testns", "x")
        assert not clean_redis.hexists(ref_x, "y")
        obj_y = make_object_key("testdomain", "testns", "y")
        assert clean_redis.hget(obj_y, "type") == "collection:dict"

    def test_reassign_dict_to_set(self, ns, clean_redis):
        ns["x"] = {"a": 1}
        ns["y"] = ns["x"]
        ns["y"] = {1, 2}
        ref_x = make_ref_key("testdomain", "testns", "x")
        assert not clean_redis.hexists(ref_x, "y")
        obj_y = make_object_key("testdomain", "testns", "y")
        assert clean_redis.hget(obj_y, "type") == "collection:set"

    def test_reassign_set_to_list(self, ns, clean_redis):
        ns["x"] = {1, 2}
        ns["y"] = ns["x"]
        ns["y"] = [3, 4]
        ref_x = make_ref_key("testdomain", "testns", "x")
        assert not clean_redis.hexists(ref_x, "y")
        obj_y = make_object_key("testdomain", "testns", "y")
        assert clean_redis.hget(obj_y, "type") == "collection:list"


class TestNamespaceThreading:
    def test_concurrent_writes_no_data_loss(self, domain):
        ns = domain.ensure_namespace("ns1")
        n_threads = 10
        n_writes = 50
        errors = []

        def writer(thread_id):
            try:
                for i in range(n_writes):
                    key = f"t{thread_id}_k{i}"
                    ns[key] = thread_id * 1000 + i
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(tid,)) for tid in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        for tid in range(n_threads):
            for i in range(n_writes):
                key = f"t{tid}_k{i}"
                assert ns[key] == tid * 1000 + i

    def test_concurrent_reads_consistent(self, domain):
        ns = domain.ensure_namespace("ns2")
        for i in range(100):
            ns[f"key{i}"] = i

        results = {}
        errors = []

        def reader(thread_id):
            try:
                local = {}
                for i in range(100):
                    local[f"key{i}"] = ns[f"key{i}"]
                results[thread_id] = local
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader, args=(tid,)) for tid in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        for tid, data in results.items():
            for i in range(100):
                assert data[f"key{i}"] == i

    def test_concurrent_read_write_no_crash(self, domain):
        ns = domain.ensure_namespace("ns3")
        for i in range(50):
            ns[f"key{i}"] = i

        errors = []

        def writer():
            try:
                for i in range(50, 100):
                    ns[f"key{i}"] = i
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for i in range(50):
                    _ = ns.get(f"key{i}")
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=writer)
        t2 = threading.Thread(target=reader)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert errors == []


class TestNamespaceErrorPaths:
    def test_getitem_missing_key(self, ns):
        with pytest.raises(KeyError):
            ns["nonexistent"]

    def test_delitem_missing_key(self, ns):
        with pytest.raises(KeyError):
            del ns["nonexistent"]

    def test_setitem_invalid_name(self, ns):
        with pytest.raises(ValueError):
            ns["__reserved__"] = 42

    def test_setitem_invalid_chars(self, ns):
        with pytest.raises(ValueError):
            ns["has space"] = 42

    def test_setitem_cross_namespace_ref(self):
        from gpdatacached.config import GPDCDomainConfig
        from gpdatacached.domain import GPDCDomain
        import redis as redis_lib

        r = redis_lib.Redis(host="127.0.0.1", port=6379, db=1, password="123456", decode_responses=True)
        r.flushdb()
        cfg = GPDCDomainConfig(redis_host="127.0.0.1", redis_port=6379, redis_db=1, redis_password="123456")
        domain = GPDCDomain(cfg, name="testdomain")
        ns1 = domain.ensure_namespace("ns1")
        ns2 = domain.ensure_namespace("ns2")
        ns1["lst"] = [1, 2, 3]
        lst = ns1.get_object("lst")
        with pytest.raises(ValueError, match="Cross-namespace"):
            ns2["ref"] = lst
        domain.close()

    def test_setitem_unsupported_type(self, ns):
        with pytest.raises(TypeError, match="Unsupported Python type"):
            ns["x"] = complex(1, 2)
