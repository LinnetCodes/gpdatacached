import time

import pytest

from gpdatacached.config import GPDCDomainConfig
from gpdatacached.domain import GPDCDomain

REDIS_HOST = "127.0.0.1"
REDIS_PORT = 6379
REDIS_DB = 1
REDIS_PASSWORD = "123456"

DOMAIN = "testdomain_ttl_expiry"
NS = "testns_ttl_expiry"

pytestmark = pytest.mark.slow


@pytest.fixture()
def domain_and_ns():
    cfg = GPDCDomainConfig(
        redis_host=REDIS_HOST,
        redis_port=REDIS_PORT,
        redis_db=REDIS_DB,
        redis_password=REDIS_PASSWORD,
    )
    d = GPDCDomain(DOMAIN, cfg)
    ns = d.get_namespace(NS)
    yield ns
    d.close()


class TestTTLActualExpiry:
    def test_ttl_object_disappears_after_expiry(self, domain_and_ns):
        ns = domain_and_ns

        ns["x"] = 42
        obj = ns.get_object("x")
        obj.cache_mode = "ttl"
        obj.ttl = 1

        assert "x" in ns
        assert ns["x"] == 42

        time.sleep(1.1)

        assert "x" not in ns
        with pytest.raises(KeyError):
            _ = ns["x"]
        assert ns.get("x") is None

    def test_sliding_object_disappears_after_expiry(self, domain_and_ns):
        ns = domain_and_ns

        ns["y"] = "hello"
        obj = ns.get_object("y")
        obj.cache_mode = "sliding"
        obj.ttl = 1

        assert "y" in ns

        time.sleep(1.1)

        assert "y" not in ns
        with pytest.raises(KeyError):
            _ = ns["y"]

    def test_ttl_container_disappears_after_expiry(self, domain_and_ns):
        ns = domain_and_ns

        ns["mylist"] = [1, 2, 3]
        obj = ns.get_object("mylist")
        obj.cache_mode = "ttl"
        obj.ttl = 1

        assert "mylist" in ns

        time.sleep(1.1)

        assert "mylist" not in ns
        with pytest.raises(KeyError):
            _ = ns["mylist"]
