import logging

import pytest
from gpdatacached.domain import GPDCDomain, GPDC_VERSION
from gpdatacached.config import GPDCDomainConfig
from gpdatacached.keys import (
    GLOBAL_DOMAINS_KEY,
    GLOBAL_META_KEY,
    make_domain_meta_key,
    make_domain_namespaces_key,
    make_namespace_meta_key,
)


REDIS_HOST = "127.0.0.1"
REDIS_PORT = 6379
REDIS_DB = 1
REDIS_PASSWORD = "123456"


def _make_config():
    return GPDCDomainConfig(
        redis_host=REDIS_HOST,
        redis_port=REDIS_PORT,
        redis_db=REDIS_DB,
        redis_password=REDIS_PASSWORD,
    )


class TestGPDCDomainInit:
    def test_domain_registers_in_global_domains(self, domain, clean_redis):
        members = clean_redis.smembers(GLOBAL_DOMAINS_KEY)
        assert "testdomain" in members

    def test_global_meta_version_set(self, domain, clean_redis):
        version = clean_redis.hget(GLOBAL_META_KEY, "version")
        assert version == GPDC_VERSION

    def test_domain_meta_written(self, domain, clean_redis):
        meta = clean_redis.hgetall(make_domain_meta_key("testdomain"))
        assert "default_cache_mode" in meta
        assert meta["default_cache_mode"] == "permanent"

    def test_domain_name_property(self, domain):
        assert domain.name == "testdomain"

    def test_domain_redis_accessible(self, domain):
        r = domain.redis
        assert r is not None
        r.ping()

    def test_incompatible_version_raises(self, clean_redis, domain_config):
        clean_redis.hset(GLOBAL_META_KEY, "version", "99.99.99")
        with pytest.raises(RuntimeError, match="Incompatible GPDC version"):
            GPDCDomain("testdomain", domain_config)

    def test_reconnect_compatible_version_ok(self, domain, domain_config):
        domain2 = GPDCDomain("anotherdomain", domain_config)
        assert domain2.name == "anotherdomain"

    def test_invalid_domain_name_raises(self, domain_config):
        with pytest.raises(ValueError):
            GPDCDomain("my:invalid:domain", domain_config)


class TestGPDCDomainEnsureNamespace:
    def test_ensure_namespace_returns_namespace(self, domain):
        from gpdatacached.namespace import GPDCNamespace

        ns = domain.ensure_namespace("myns")
        assert isinstance(ns, GPDCNamespace)
        assert ns.name == "myns"

    def test_ensure_namespace_cached(self, domain):
        ns1 = domain.ensure_namespace("myns")
        ns2 = domain.ensure_namespace("myns")
        assert ns1 is ns2

    def test_ensure_namespace_different_names(self, domain):
        ns1 = domain.ensure_namespace("ns1")
        ns2 = domain.ensure_namespace("ns2")
        assert ns1 is not ns2
        assert ns1.name == "ns1"
        assert ns2.name == "ns2"


class TestGPDCDomainContainerProtocol:
    def test_contains_returns_true_for_existing_namespace(self, domain):
        domain.ensure_namespace("myns")
        assert "myns" in domain

    def test_contains_returns_false_for_nonexistent_namespace(self, domain):
        assert "nonexistent" not in domain

    def test_iter_yields_namespace_names(self, domain):
        domain.ensure_namespace("ns1")
        domain.ensure_namespace("ns2")
        names = list(domain)
        assert set(names) == {"ns1", "ns2"}

    def test_iter_empty_domain_yields_nothing(self, domain):
        assert list(domain) == []

    def test_len_returns_namespace_count(self, domain):
        domain.ensure_namespace("ns1")
        domain.ensure_namespace("ns2")
        domain.ensure_namespace("ns3")
        assert len(domain) == 3

    def test_len_empty_domain_is_zero(self, domain):
        assert len(domain) == 0

    def test_getitem_returns_existing_namespace(self, domain):
        from gpdatacached.namespace import GPDCNamespace

        ns = domain.ensure_namespace("myns")
        result = domain["myns"]
        assert result is ns
        assert isinstance(result, GPDCNamespace)

    def test_getitem_raises_keyerror_for_nonexistent(self, domain):
        with pytest.raises(KeyError):
            _ = domain["doesnotexist"]

    def test_getitem_does_not_create_namespace(self, domain):
        with pytest.raises(KeyError):
            _ = domain["newns"]
        assert "newns" not in domain


class TestGPDCDomainRedisSource:
    def _make_domain(self, domain_config):
        return GPDCDomain("testdomain", domain_config)

    def test_contains_sees_namespace_registered_by_previous_session(self, clean_redis, domain_config):
        d1 = self._make_domain(domain_config)
        d1.ensure_namespace("myns")

        d2 = self._make_domain(domain_config)
        assert "myns" in d2

    def test_contains_returns_false_for_namespace_absent_from_redis(self, clean_redis, domain_config):
        d = self._make_domain(domain_config)
        assert "ghost" not in d

    def test_iter_yields_namespaces_registered_by_previous_session(self, clean_redis, domain_config):
        d1 = self._make_domain(domain_config)
        d1.ensure_namespace("ns1")
        d1.ensure_namespace("ns2")

        d2 = self._make_domain(domain_config)
        assert set(d2) == {"ns1", "ns2"}

    def test_len_counts_namespaces_from_redis(self, clean_redis, domain_config):
        d1 = self._make_domain(domain_config)
        d1.ensure_namespace("ns1")
        d1.ensure_namespace("ns2")

        d2 = self._make_domain(domain_config)
        assert len(d2) == 2

    def test_getitem_returns_namespace_registered_by_previous_session(self, clean_redis, domain_config):
        from gpdatacached.namespace import GPDCNamespace

        d1 = self._make_domain(domain_config)
        d1.ensure_namespace("myns")

        d2 = self._make_domain(domain_config)
        ns = d2["myns"]
        assert isinstance(ns, GPDCNamespace)
        assert ns.name == "myns"

    def test_getitem_raises_keyerror_for_namespace_absent_from_redis(self, clean_redis, domain_config):
        d = self._make_domain(domain_config)
        with pytest.raises(KeyError):
            _ = d["neverexisted"]

    def test_getitem_does_not_register_namespace_in_redis_on_miss(self, clean_redis, domain_config):
        d = self._make_domain(domain_config)
        with pytest.raises(KeyError):
            _ = d["ghost"]
        members = clean_redis.smembers(make_domain_namespaces_key("testdomain"))
        assert "ghost" not in members


class TestGPDCDomainPredefinedNamespaces:
    def test_predefined_namespaces_registered_in_redis(self, domain_with_namespaces):
        assert "ns_1" in domain_with_namespaces
        assert "ns_2" in domain_with_namespaces

    def test_predefined_namespace_count_matches_config(self, domain_with_namespaces):
        assert len(domain_with_namespaces) == 2

    def test_predefined_namespace_uses_per_namespace_cache_mode(self, domain_with_namespaces, clean_redis):
        meta = clean_redis.hgetall(make_namespace_meta_key("testdomain", "ns_1"))
        assert meta["default_cache_mode"] == "ttl"

    def test_predefined_namespace_uses_per_namespace_ttl(self, domain_with_namespaces, clean_redis):
        meta = clean_redis.hgetall(make_namespace_meta_key("testdomain", "ns_1"))
        assert meta["default_ttl_seconds"] == "3600"

    def test_second_predefined_namespace_uses_its_own_config(self, domain_with_namespaces, clean_redis):
        meta = clean_redis.hgetall(make_namespace_meta_key("testdomain", "ns_2"))
        assert meta["default_cache_mode"] == "sliding"
        assert meta["default_ttl_seconds"] == "7200"

    def test_predefined_namespace_accessible_via_getitem(self, domain_with_namespaces):
        from gpdatacached.namespace import GPDCNamespace

        ns = domain_with_namespaces["ns_1"]
        assert isinstance(ns, GPDCNamespace)
        assert ns.name == "ns_1"

    def test_predefined_namespace_config_not_overwritten_on_reconnect(self, clean_redis, domain_config_with_namespaces):
        from gpdatacached.domain import GPDCDomain
        from gpdatacached.keys import make_namespace_meta_key

        GPDCDomain("testdomain", domain_config_with_namespaces)

        meta_key = make_namespace_meta_key("testdomain", "ns_1")
        clean_redis.hset(meta_key, "default_ttl_seconds", "9999")

        GPDCDomain("testdomain", domain_config_with_namespaces)
        meta = clean_redis.hgetall(meta_key)
        assert meta["default_ttl_seconds"] == "9999"


class TestMultiDomainIsolation:
    def test_same_key_different_values(self, clean_redis, domain_config):
        d1 = GPDCDomain("domain_a", domain_config)
        d2 = GPDCDomain("domain_b", domain_config)
        ns1 = d1.ensure_namespace("shared_ns")
        ns2 = d2.ensure_namespace("shared_ns")

        ns1["x"] = 1
        ns2["x"] = 999

        assert ns1["x"] == 1
        assert ns2["x"] == 999

        d1.close()
        d2.close()

    def test_delete_in_one_domain_does_not_affect_other(self, clean_redis, domain_config):
        d1 = GPDCDomain("domain_a", domain_config)
        d2 = GPDCDomain("domain_b", domain_config)
        ns1 = d1.ensure_namespace("shared_ns")
        ns2 = d2.ensure_namespace("shared_ns")

        ns1["x"] = 1
        ns2["x"] = 2

        del ns1["x"]

        assert "x" not in ns1
        assert ns2["x"] == 2

        d1.close()
        d2.close()

    def test_iter_len_independent(self, clean_redis, domain_config):
        d1 = GPDCDomain("domain_a", domain_config)
        d2 = GPDCDomain("domain_b", domain_config)
        ns1 = d1.ensure_namespace("ns")
        ns2 = d2.ensure_namespace("ns")

        ns1["a"] = 1
        ns1["b"] = 2
        ns2["z"] = 99

        assert len(ns1) == 2
        assert len(ns2) == 1
        assert sorted(ns1) == ["a", "b"]
        assert list(ns2) == ["z"]

        d1.close()
        d2.close()

    def test_namespace_isolation_in_domain_iter(self, clean_redis, domain_config):
        d1 = GPDCDomain("domain_a", domain_config)
        d2 = GPDCDomain("domain_b", domain_config)

        d1.ensure_namespace("only_in_a")
        d2.ensure_namespace("only_in_b")

        assert "only_in_a" in d1
        assert "only_in_a" not in d2
        assert "only_in_b" in d2
        assert "only_in_b" not in d1

        d1.close()
        d2.close()


class TestDeleteNamespace:
    def test_removes_namespace_from_domain(self, domain, clean_redis):
        ns = domain.ensure_namespace("ns1")
        ns["x"] = 42
        ns["y"] = [1, 2, 3]

        domain.delete_namespace("ns1")

        assert "ns1" not in domain
        assert len(domain) == 0

    def test_deletes_all_namespace_keys(self, domain, clean_redis):
        ns = domain.ensure_namespace("ns1")
        ns["x"] = 42
        ns["y"] = [1, 2, 3]
        ns["z"] = {"a": 1}

        domain.delete_namespace("ns1")

        pattern = "gpdc:testdomain:ns1:*"
        cursor, keys = clean_redis.scan(cursor=0, match=pattern, count=100)
        assert keys == []

    def test_removes_namespace_meta(self, domain, clean_redis):
        domain.ensure_namespace("ns1")
        meta_key = make_namespace_meta_key("testdomain", "ns1")

        domain.delete_namespace("ns1")

        assert clean_redis.exists(meta_key) == 0

    def test_removes_from_namespaces_set(self, domain, clean_redis):
        domain.ensure_namespace("ns1")
        ns_key = make_domain_namespaces_key("testdomain")

        domain.delete_namespace("ns1")

        assert clean_redis.sismember(ns_key, "ns1") == 0

    def test_removes_from_memory_cache(self, domain):
        domain.ensure_namespace("ns1")
        assert "ns1" in domain._namespaces

        domain.delete_namespace("ns1")

        assert "ns1" not in domain._namespaces

    def test_raises_keyerror_for_nonexistent(self, domain):
        with pytest.raises(KeyError):
            domain.delete_namespace("does_not_exist")

    def test_other_namespaces_unaffected(self, domain):
        ns1 = domain.ensure_namespace("ns1")
        ns2 = domain.ensure_namespace("ns2")
        ns1["x"] = 42
        ns2["y"] = 99

        domain.delete_namespace("ns1")

        assert "ns1" not in domain
        assert "ns2" in domain
        assert ns2["y"] == 99

    def test_visible_across_sessions(self, clean_redis):
        cfg = _make_config()
        d1 = GPDCDomain("testdomain", cfg)
        d1.ensure_namespace("ns1")
        d1.ensure_namespace("ns2")
        d1.delete_namespace("ns1")
        d1.close()

        d2 = GPDCDomain("testdomain", cfg)
        assert "ns1" not in d2
        assert "ns2" in d2
        d2.close()


class TestNamespaceDestroy:
    def test_destroy_removes_namespace(self, domain, clean_redis):
        ns = domain.ensure_namespace("ns1")
        ns["x"] = 42

        ns.destroy()

        assert "ns1" not in domain

    def test_destroy_deletes_all_keys(self, domain, clean_redis):
        ns = domain.ensure_namespace("ns1")
        ns["x"] = 42

        ns.destroy()

        pattern = "gpdc:testdomain:ns1:*"
        cursor, keys = clean_redis.scan(cursor=0, match=pattern, count=100)
        assert keys == []


class TestDomainDestroy:
    def test_removes_domain_from_global_set(self, clean_redis):
        cfg = _make_config()
        d = GPDCDomain("testdomain", cfg)
        d.destroy()

        assert clean_redis.sismember(GLOBAL_DOMAINS_KEY, "testdomain") == 0

    def test_deletes_domain_meta(self, clean_redis):
        cfg = _make_config()
        d = GPDCDomain("testdomain", cfg)
        meta_key = make_domain_meta_key("testdomain")

        d.destroy()

        assert clean_redis.exists(meta_key) == 0

    def test_deletes_domain_namespaces_set(self, clean_redis):
        cfg = _make_config()
        d = GPDCDomain("testdomain", cfg)
        d.ensure_namespace("ns1")
        ns_key = make_domain_namespaces_key("testdomain")

        d.destroy()

        assert clean_redis.exists(ns_key) == 0

    def test_deletes_all_namespace_data(self, clean_redis):
        cfg = _make_config()
        d = GPDCDomain("testdomain", cfg)
        ns = d.ensure_namespace("ns1")
        ns["x"] = 42
        ns["y"] = {"a": 1}

        d.destroy()

        pattern = "gpdc:testdomain:*"
        cursor, keys = clean_redis.scan(cursor=0, match=pattern, count=100)
        assert keys == []

    def test_closes_domain(self, clean_redis):
        cfg = _make_config()
        d = GPDCDomain("testdomain", cfg)
        assert not d._closed

        d.destroy()

        assert d._closed
        with pytest.raises(RuntimeError, match="closed"):
            d.redis

    def test_multiple_namespaces_all_cleaned(self, clean_redis):
        cfg = _make_config()
        d = GPDCDomain("testdomain", cfg)
        d.ensure_namespace("ns1")
        d.ensure_namespace("ns2")
        d.ensure_namespace("ns3")

        d.destroy()

        assert clean_redis.sismember(GLOBAL_DOMAINS_KEY, "testdomain") == 0
        ns_key = make_domain_namespaces_key("testdomain")
        assert clean_redis.exists(ns_key) == 0


class TestDomainClose:
    def test_close_is_idempotent(self, domain):
        domain.close()
        domain.close()

    def test_redis_property_raises_after_close(self, domain):
        domain.close()
        with pytest.raises(RuntimeError, match="closed"):
            _ = domain.redis

    def test_ensure_namespace_raises_after_close(self, domain):
        domain.close()
        with pytest.raises(RuntimeError, match="closed"):
            domain.ensure_namespace("any")

    def test_contains_raises_after_close(self, domain):
        domain.close()
        with pytest.raises(RuntimeError, match="closed"):
            _ = "x" in domain

    def test_iter_raises_after_close(self, domain):
        domain.close()
        with pytest.raises(RuntimeError, match="closed"):
            list(domain)

    def test_len_raises_after_close(self, domain):
        domain.close()
        with pytest.raises(RuntimeError, match="closed"):
            len(domain)

    def test_getitem_raises_after_close(self, domain):
        domain.close()
        with pytest.raises(RuntimeError, match="closed"):
            _ = domain["x"]

    def test_run_gc_raises_after_close(self, domain):
        domain.close()
        with pytest.raises(RuntimeError, match="closed"):
            domain.run_gc()

    def test_context_manager_closes_domain(self, clean_redis, domain_config):
        with GPDCDomain("testdomain", domain_config) as d:
            _ = d.redis
        with pytest.raises(RuntimeError, match="closed"):
            _ = d.redis

    def test_del_auto_closes_with_warning(self, clean_redis, domain_config, caplog):
        d = GPDCDomain("testdomain", domain_config)
        assert not d._closed
        with caplog.at_level(logging.WARNING, logger="gpdatacached.domain"):
            del d
        assert "not explicitly closed" in caplog.text


class TestDomainContextManager:
    def test_domain_as_context_manager(self, clean_redis, domain_config):
        from gpdatacached.domain import GPDCDomain

        domain_config.gc_enabled = True
        with GPDCDomain("testdomain", domain_config) as domain:
            assert domain.name == "testdomain"
            ns = domain.ensure_namespace("testns")
            ns["x"] = 42
            assert ns["x"] == 42
        assert domain._gc_thread is None or not domain._gc_thread.is_alive()

    def test_domain_context_manager_closes_on_exception(self, clean_redis, domain_config):
        from gpdatacached.domain import GPDCDomain

        domain_config.gc_enabled = True
        domain = None
        try:
            with GPDCDomain("testdomain", domain_config) as d:
                domain = d
                raise ValueError("test error")
        except ValueError:
            pass
        assert domain._gc_thread is None or not domain._gc_thread.is_alive()


class TestGPDCDomainGetNamespaceReadOnly:
    def test_get_namespace_returns_existing(self, domain):
        domain.ensure_namespace("myns")
        ns = domain.get_namespace("myns")
        assert ns.name == "myns"

    def test_get_namespace_raises_for_nonexistent(self, domain):
        with pytest.raises(KeyError):
            domain.get_namespace("doesnotexist")

    def test_get_namespace_does_not_create(self, domain):
        with pytest.raises(KeyError):
            domain.get_namespace("newns")
        assert "newns" not in domain

    def test_get_namespace_raises_after_close(self, domain):
        domain.close()
        with pytest.raises(RuntimeError, match="closed"):
            domain.get_namespace("any")


class TestDomainMetaRedisSource:
    def test_get_default_cache_mode_reads_from_redis(self, clean_redis, domain_config):
        d = GPDCDomain("testdomain", domain_config)
        assert d.get_default_cache_mode() == "permanent"
        d.close()

    def test_get_default_ttl_seconds_reads_from_redis(self, clean_redis, domain_config):
        d = GPDCDomain("testdomain", domain_config)
        assert d.get_default_ttl_seconds() == 0
        d.close()

    def test_second_connection_reads_redis_not_config(self, clean_redis, domain_config):
        cfg_ttl = GPDCDomainConfig(
            redis_host=REDIS_HOST, redis_port=REDIS_PORT,
            redis_db=REDIS_DB, redis_password=REDIS_PASSWORD,
            default_cache_mode="ttl", default_ttl_seconds=300,
        )
        d1 = GPDCDomain("testdomain", cfg_ttl)
        assert d1.get_default_cache_mode() == "ttl"
        assert d1.get_default_ttl_seconds() == 300
        d1.close()

        cfg_permanent = GPDCDomainConfig(
            redis_host=REDIS_HOST, redis_port=REDIS_PORT,
            redis_db=REDIS_DB, redis_password=REDIS_PASSWORD,
            default_cache_mode="permanent", default_ttl_seconds=0,
        )
        d2 = GPDCDomain("testdomain", cfg_permanent)
        assert d2.get_default_cache_mode() == "ttl"
        assert d2.get_default_ttl_seconds() == 300
        d2.close()

    def test_namespace_defaults_come_from_redis_namespace_meta(self, clean_redis):
        cfg_ttl = GPDCDomainConfig(
            redis_host=REDIS_HOST, redis_port=REDIS_PORT,
            redis_db=REDIS_DB, redis_password=REDIS_PASSWORD,
            default_cache_mode="ttl", default_ttl_seconds=300,
        )
        d1 = GPDCDomain("testdomain", cfg_ttl)
        ns1 = d1.ensure_namespace("myns")
        assert ns1._get_default_cache_mode() == "ttl"
        assert ns1._get_default_ttl_seconds() == 300
        d1.close()

        cfg_permanent = GPDCDomainConfig(
            redis_host=REDIS_HOST, redis_port=REDIS_PORT,
            redis_db=REDIS_DB, redis_password=REDIS_PASSWORD,
            default_cache_mode="permanent", default_ttl_seconds=0,
        )
        d2 = GPDCDomain("testdomain", cfg_permanent)
        ns2 = d2.ensure_namespace("myns")
        assert ns2._get_default_cache_mode() == "ttl"
        assert ns2._get_default_ttl_seconds() == 300
        d2.close()
