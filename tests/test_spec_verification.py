import datetime

from gpdatacached import GPDCNamespace
from gpdatacached.keys import GLOBAL_META_KEY


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

        ns["test_int"] = 42
        ns["test_float"] = 42.2
        ns["test_bool"] = True
        ns["test_str"] = "hello, world"
        ns["test_datetime"] = datetime.datetime(2020, 1, 2)
        ns["test_date"] = datetime.date(2020, 1, 1)
        ns["test_time"] = datetime.time(0, 0, 0)
        ns["test_none"] = None

        assert len(ns) == 8
        for name in ns:
            assert name in {
                "test_int",
                "test_float",
                "test_bool",
                "test_str",
                "test_datetime",
                "test_date",
                "test_time",
                "test_none",
            }
        for name in {
            "test_int",
            "test_float",
            "test_bool",
            "test_str",
            "test_datetime",
            "test_date",
            "test_time",
            "test_none",
        }:
            assert name in ns
        assert ns["test_int"] == 42
        assert ns["test_float"] == 42.2
        assert ns["test_bool"] is True
        assert ns["test_str"] == "hello, world"
        assert ns["test_datetime"] == datetime.datetime(2020, 1, 2)
        assert ns["test_date"] == datetime.date(2020, 1, 1)
        assert ns["test_time"] == datetime.time(0, 0, 0)
        assert ns["test_none"] is None

        test_ns.setdefault("test_setdefault", "hello, default")
        assert test_ns["test_setdefault"] == "hello, default"
        obj = test_ns.get_object("test_get", 100)
        assert obj.value == 100
        assert test_ns["test_get"] == 100
