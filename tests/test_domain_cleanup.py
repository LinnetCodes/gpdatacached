# tests/test_domain_cleanup.py
import pytest
from gpdatacached.domain import GPDCDomain
from gpdatacached.config import GPDCDomainConfig
from gpdatacached.keys import (
    GLOBAL_DOMAINS_KEY,
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


class TestDeleteNamespace:
    def test_removes_namespace_from_domain(self, domain, clean_redis):
        ns = domain.get_namespace("ns1")
        ns["x"] = 42
        ns["y"] = [1, 2, 3]

        domain.delete_namespace("ns1")

        assert "ns1" not in domain
        assert len(domain) == 0

    def test_deletes_all_namespace_keys(self, domain, clean_redis):
        ns = domain.get_namespace("ns1")
        ns["x"] = 42
        ns["y"] = [1, 2, 3]
        ns["z"] = {"a": 1}

        domain.delete_namespace("ns1")

        pattern = "gpdc:testdomain:ns1:*"
        cursor, keys = clean_redis.scan(cursor=0, match=pattern, count=100)
        assert keys == []

    def test_removes_namespace_meta(self, domain, clean_redis):
        domain.get_namespace("ns1")
        meta_key = make_namespace_meta_key("testdomain", "ns1")

        domain.delete_namespace("ns1")

        assert clean_redis.exists(meta_key) == 0

    def test_removes_from_namespaces_set(self, domain, clean_redis):
        domain.get_namespace("ns1")
        ns_key = make_domain_namespaces_key("testdomain")

        domain.delete_namespace("ns1")

        assert clean_redis.sismember(ns_key, "ns1") == 0

    def test_removes_from_memory_cache(self, domain):
        domain.get_namespace("ns1")
        assert "ns1" in domain._namespaces

        domain.delete_namespace("ns1")

        assert "ns1" not in domain._namespaces

    def test_raises_keyerror_for_nonexistent(self, domain):
        with pytest.raises(KeyError):
            domain.delete_namespace("does_not_exist")

    def test_other_namespaces_unaffected(self, domain):
        ns1 = domain.get_namespace("ns1")
        ns2 = domain.get_namespace("ns2")
        ns1["x"] = 42
        ns2["y"] = 99

        domain.delete_namespace("ns1")

        assert "ns1" not in domain
        assert "ns2" in domain
        assert ns2["y"] == 99

    def test_visible_across_sessions(self, clean_redis):
        cfg = _make_config()
        d1 = GPDCDomain("testdomain", cfg)
        d1.get_namespace("ns1")
        d1.get_namespace("ns2")
        d1.delete_namespace("ns1")
        d1.close()

        d2 = GPDCDomain("testdomain", cfg)
        assert "ns1" not in d2
        assert "ns2" in d2
        d2.close()


class TestNamespaceDestroy:
    def test_destroy_removes_namespace(self, domain, clean_redis):
        ns = domain.get_namespace("ns1")
        ns["x"] = 42

        ns.destroy()

        assert "ns1" not in domain

    def test_destroy_deletes_all_keys(self, domain, clean_redis):
        ns = domain.get_namespace("ns1")
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
        d.get_namespace("ns1")
        ns_key = make_domain_namespaces_key("testdomain")

        d.destroy()

        assert clean_redis.exists(ns_key) == 0

    def test_deletes_all_namespace_data(self, clean_redis):
        cfg = _make_config()
        d = GPDCDomain("testdomain", cfg)
        ns = d.get_namespace("ns1")
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
        d.get_namespace("ns1")
        d.get_namespace("ns2")
        d.get_namespace("ns3")

        d.destroy()

        assert clean_redis.sismember(GLOBAL_DOMAINS_KEY, "testdomain") == 0
        ns_key = make_domain_namespaces_key("testdomain")
        assert clean_redis.exists(ns_key) == 0
