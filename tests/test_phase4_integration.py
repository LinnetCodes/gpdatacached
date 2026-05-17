"""Integration tests for Phase 4: Set + GC + Reference Counting."""

from __future__ import annotations

import pytest
import redis as redis_lib

from gpdatacached.config import GPDCDomainConfig
from gpdatacached.domain import GPDCDomain
from gpdatacached.keys import make_raw_key, make_ref_key
from gpdatacached.objects import SetObject, ListObject, DictObject

REDIS_HOST = "127.0.0.1"
REDIS_PORT = 6379
REDIS_DB = 1
REDIS_PASSWORD = "123456"

DOMAIN = "testdomain_p4i"
NS = "testns_p4i"


@pytest.fixture()
def r():
    client = redis_lib.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD,
        decode_responses=True,
    )
    yield client
    pattern = f"gpdc:{DOMAIN}:{NS}:*"
    cursor = 0
    while True:
        cursor, keys = client.scan(cursor=cursor, match=pattern, count=100)
        if keys:
            client.delete(*keys)
        if cursor == 0:
            break
    client.close()


@pytest.fixture()
def ns(r):
    config = GPDCDomainConfig(
        redis_host=REDIS_HOST,
        redis_port=REDIS_PORT,
        redis_db=REDIS_DB,
        redis_password=REDIS_PASSWORD,
    )
    domain = GPDCDomain(DOMAIN, config)
    return domain.get_namespace(NS)


class TestSpecScenarioSection8:
    def test_set_operations(self, ns):
        ns["primes"] = {2, 3, 5, 7, 11}
        ns["evens"] = {2, 4, 6, 8, 10}
        primes = ns["primes"]
        evens = ns["evens"]
        intersection = primes & evens
        assert isinstance(intersection, set)
        assert intersection == {2}

    def test_mixed_nesting_dict_with_set_value(self, ns):
        ns["mixed"] = {"list": [1, 2], "nums": {1, 2, 3}}
        d = ns["mixed"]
        assert isinstance(d["list"], ListObject)
        assert isinstance(d["nums"], SetObject)
        assert list(d["list"]) == [1, 2]
        assert set(d["nums"]) == {1, 2, 3}

    def test_reference_and_delete(self, ns, r):
        ns["mixed"] = {"list": [1, 2], "nums": {1, 2, 3}}
        ns["ref"] = ns["mixed"]
        del ns["mixed"]
        d = ns["ref"]
        assert isinstance(d["list"], ListObject)
        assert list(d["list"]) == [1, 2]

    def test_gc_cleans_orphans_after_deletion(self, ns, r):
        ns["x"] = [1, 2, 3]
        raw_x = make_raw_key(DOMAIN, NS, "x")
        ns["y"] = ns["x"]
        del ns["x"]
        del ns["y"]
        assert not r.exists(raw_x)

    def test_three_container_types_in_dict(self, ns):
        ns["complex"] = {
            "list": [1, 2, 3],
            "dict": {"inner": 42},
            "nums": {1, 2, 3},
        }
        d = ns["complex"]
        assert isinstance(d["list"], ListObject)
        assert isinstance(d["dict"], DictObject)
        assert isinstance(d["nums"], SetObject)

    def test_set_in_list(self, ns):
        ns["mylist"] = [1, {2, 3}, "hello"]
        lst = ns["mylist"]
        inner = lst[1]
        assert isinstance(inner, SetObject)
        assert set(inner) == {2, 3}
        assert lst[2] == "hello"

    def test_set_reference_sharing(self, ns, r):
        ns["s1"] = {1, 2, 3}
        ns["s2"] = ns["s1"]
        ref_s1 = make_ref_key(DOMAIN, NS, "s1")
        assert "s2" in r.smembers(ref_s1)
        s1 = ns["s1"]
        s1.add(4)
        s2 = ns["s2"]
        assert 4 in s2

    def test_full_lifecycle(self, ns, r):
        ns["data"] = {
            "list": [1, 2],
            "set": {3, 4},
            "nested": {"a": 5},
        }
        ns["backup"] = ns["data"]
        del ns["data"]
        backup = ns["backup"]
        assert list(backup["list"]) == [1, 2]
        assert set(backup["set"]) == {3, 4}
        assert backup["nested"]["a"] == 5

        del ns["backup"]
        ns.domain.run_gc(namespace=NS)
