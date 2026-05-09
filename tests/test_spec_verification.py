import datetime
import pytest

from gpdatacached import GPDCNamespace
from gpdatacached.keys import GLOBAL_META_KEY, make_domain_meta_key, make_namespace_meta_key, make_object_key


class TestSpecVerification:
    """Spec verification tests"""

    def test_domain_ops(self, domain, clean_redis):
        ns = domain.get_namespace("spec_verification")
        test_ns = domain.get_namespace("test_ns")

        assert clean_redis.hget(GLOBAL_META_KEY, "version") == "0.1.0"
        assert "testdomain" in clean_redis.smembers("gpdc:__domains__")
        assert clean_redis.hget("gpdc:testdomain:__meta__", "default_cache_mode") == "permanent"
        assert clean_redis.hget("gpdc:testdomain:__meta__", "default_ttl_seconds") == "0"
        assert "spec_verification" in clean_redis.smembers("gpdc:testdomain:__namespaces__")
        assert clean_redis.hget("gpdc:testdomain:spec_verification:__meta__", "default_cache_mode") == "permanent"
        assert clean_redis.hget("gpdc:testdomain:spec_verification:__meta__", "default_ttl_seconds") == "0"

        assert domain.name == "testdomain"
        assert "spec_verification" in domain
        assert "test_ns" in domain
        assert len(domain) == 2
        for name in domain:
            assert name in {"spec_verification", "test_ns"}
        assert ns is domain["spec_verification"]
        assert isinstance(domain["test_ns"], GPDCNamespace)

        ns["counter"] = 42
        test_ns["test_str"] = "hello, gpdc"
