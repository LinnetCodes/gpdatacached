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
