# tests/test_domain.py
import pytest
from gpdatacached.domain import GPDCDomain, GPDC_VERSION
from gpdatacached.keys import GLOBAL_META_KEY, GLOBAL_DOMAINS_KEY, make_domain_meta_key


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
        r.ping()  # Should not raise

    def test_incompatible_version_raises(self, clean_redis, domain_config):
        # Manually set wrong version
        clean_redis.hset(GLOBAL_META_KEY, "version", "99.99.99")
        with pytest.raises(RuntimeError, match="Incompatible GPDC version"):
            GPDCDomain("testdomain", domain_config)

    def test_reconnect_compatible_version_ok(self, domain, domain_config):
        # A second domain connecting to same Redis should succeed (same version)
        domain2 = GPDCDomain("anotherdomain", domain_config)
        assert domain2.name == "anotherdomain"

    def test_invalid_domain_name_raises(self, domain_config):
        with pytest.raises(ValueError):
            GPDCDomain("my:invalid:domain", domain_config)


class TestGPDCDomainGetNamespace:
    def test_get_namespace_returns_namespace(self, domain):
        from gpdatacached.namespace import GPDCNamespace

        ns = domain.get_namespace("myns")
        assert isinstance(ns, GPDCNamespace)
        assert ns.name == "myns"

    def test_get_namespace_cached(self, domain):
        ns1 = domain.get_namespace("myns")
        ns2 = domain.get_namespace("myns")
        assert ns1 is ns2

    def test_get_namespace_different_names(self, domain):
        ns1 = domain.get_namespace("ns1")
        ns2 = domain.get_namespace("ns2")
        assert ns1 is not ns2
        assert ns1.name == "ns1"
        assert ns2.name == "ns2"


class TestGPDCDomainContainerProtocol:
    def test_contains_returns_true_for_existing_namespace(self, domain):
        domain.get_namespace("myns")
        assert "myns" in domain

    def test_contains_returns_false_for_nonexistent_namespace(self, domain):
        assert "nonexistent" not in domain

    def test_iter_yields_namespace_names(self, domain):
        domain.get_namespace("ns1")
        domain.get_namespace("ns2")
        names = list(domain)
        assert set(names) == {"ns1", "ns2"}

    def test_iter_empty_domain_yields_nothing(self, domain):
        assert list(domain) == []

    def test_len_returns_namespace_count(self, domain):
        domain.get_namespace("ns1")
        domain.get_namespace("ns2")
        domain.get_namespace("ns3")
        assert len(domain) == 3

    def test_len_empty_domain_is_zero(self, domain):
        assert len(domain) == 0

    def test_getitem_returns_existing_namespace(self, domain):
        from gpdatacached.namespace import GPDCNamespace

        ns = domain.get_namespace("myns")
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


class TestGPDCDomainContainerProtocolRedisSource:
    """Container protocol methods must use Redis as authoritative source,
    not just the in-memory session cache."""

    def _make_domain(self, domain_config):
        return GPDCDomain("testdomain", domain_config)

    def test_contains_sees_namespace_registered_by_previous_session(self, clean_redis, domain_config):
        # Session 1: create namespace → writes to Redis
        d1 = self._make_domain(domain_config)
        d1.get_namespace("myns")

        # Session 2: fresh memory cache, same Redis data
        d2 = self._make_domain(domain_config)
        assert "myns" in d2

    def test_contains_returns_false_for_namespace_absent_from_redis(self, clean_redis, domain_config):
        d = self._make_domain(domain_config)
        assert "ghost" not in d

    def test_iter_yields_namespaces_registered_by_previous_session(self, clean_redis, domain_config):
        d1 = self._make_domain(domain_config)
        d1.get_namespace("ns1")
        d1.get_namespace("ns2")

        d2 = self._make_domain(domain_config)
        assert set(d2) == {"ns1", "ns2"}

    def test_len_counts_namespaces_from_redis(self, clean_redis, domain_config):
        d1 = self._make_domain(domain_config)
        d1.get_namespace("ns1")
        d1.get_namespace("ns2")

        d2 = self._make_domain(domain_config)
        assert len(d2) == 2

    def test_getitem_returns_namespace_registered_by_previous_session(self, clean_redis, domain_config):
        from gpdatacached.namespace import GPDCNamespace

        d1 = self._make_domain(domain_config)
        d1.get_namespace("myns")

        d2 = self._make_domain(domain_config)
        ns = d2["myns"]
        assert isinstance(ns, GPDCNamespace)
        assert ns.name == "myns"

    def test_getitem_raises_keyerror_for_namespace_absent_from_redis(self, clean_redis, domain_config):
        d = self._make_domain(domain_config)
        with pytest.raises(KeyError):
            _ = d["neverexisted"]

    def test_getitem_does_not_register_namespace_in_redis_on_miss(self, clean_redis, domain_config):
        from gpdatacached.keys import make_domain_namespaces_key

        d = self._make_domain(domain_config)
        with pytest.raises(KeyError):
            _ = d["ghost"]
        members = clean_redis.smembers(make_domain_namespaces_key("testdomain"))
        assert "ghost" not in members


class TestGPDCDomainPredefinedNamespaces:
    """GPDCDomain should auto-create predefined namespaces from config on init."""

    def test_predefined_namespaces_registered_in_redis(self, domain_with_namespaces):
        assert "ns_1" in domain_with_namespaces
        assert "ns_2" in domain_with_namespaces

    def test_predefined_namespace_count_matches_config(self, domain_with_namespaces):
        assert len(domain_with_namespaces) == 2

    def test_predefined_namespace_uses_per_namespace_cache_mode(self, domain_with_namespaces, clean_redis):
        from gpdatacached.keys import make_namespace_meta_key

        meta = clean_redis.hgetall(make_namespace_meta_key("testdomain", "ns_1"))
        assert meta["default_cache_mode"] == "ttl"

    def test_predefined_namespace_uses_per_namespace_ttl(self, domain_with_namespaces, clean_redis):
        from gpdatacached.keys import make_namespace_meta_key

        meta = clean_redis.hgetall(make_namespace_meta_key("testdomain", "ns_1"))
        assert meta["default_ttl_seconds"] == "3600"

    def test_second_predefined_namespace_uses_its_own_config(self, domain_with_namespaces, clean_redis):
        from gpdatacached.keys import make_namespace_meta_key

        meta = clean_redis.hgetall(make_namespace_meta_key("testdomain", "ns_2"))
        assert meta["default_cache_mode"] == "sliding"
        assert meta["default_ttl_seconds"] == "7200"

    def test_predefined_namespace_accessible_via_getitem(self, domain_with_namespaces):
        from gpdatacached.namespace import GPDCNamespace

        ns = domain_with_namespaces["ns_1"]
        assert isinstance(ns, GPDCNamespace)
        assert ns.name == "ns_1"

    def test_predefined_namespace_config_not_overwritten_on_reconnect(self, clean_redis, domain_config_with_namespaces):
        """If namespace already exists in Redis, per-namespace config must not
        overwrite the existing metadata on a subsequent domain connection."""
        from gpdatacached.domain import GPDCDomain
        from gpdatacached.keys import make_namespace_meta_key

        # First session: namespace created with per-namespace config (ttl=3600)
        GPDCDomain("testdomain", domain_config_with_namespaces)

        # Simulate a runtime change to namespace metadata in Redis
        meta_key = make_namespace_meta_key("testdomain", "ns_1")
        clean_redis.hset(meta_key, "default_ttl_seconds", "9999")

        # Second session with same config: must NOT revert to 3600
        GPDCDomain("testdomain", domain_config_with_namespaces)
        meta = clean_redis.hgetall(meta_key)
        assert meta["default_ttl_seconds"] == "9999"


class TestMultiDomainIsolation:
    def test_same_key_different_values(self, clean_redis, domain_config):
        d1 = GPDCDomain("domain_a", domain_config)
        d2 = GPDCDomain("domain_b", domain_config)
        ns1 = d1.get_namespace("shared_ns")
        ns2 = d2.get_namespace("shared_ns")

        ns1["x"] = 1
        ns2["x"] = 999

        assert ns1["x"] == 1
        assert ns2["x"] == 999

        d1.close()
        d2.close()

    def test_delete_in_one_domain_does_not_affect_other(self, clean_redis, domain_config):
        d1 = GPDCDomain("domain_a", domain_config)
        d2 = GPDCDomain("domain_b", domain_config)
        ns1 = d1.get_namespace("shared_ns")
        ns2 = d2.get_namespace("shared_ns")

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
        ns1 = d1.get_namespace("ns")
        ns2 = d2.get_namespace("ns")

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

        d1.get_namespace("only_in_a")
        d2.get_namespace("only_in_b")

        assert "only_in_a" in d1
        assert "only_in_a" not in d2
        assert "only_in_b" in d2
        assert "only_in_b" not in d1

        d1.close()
        d2.close()
