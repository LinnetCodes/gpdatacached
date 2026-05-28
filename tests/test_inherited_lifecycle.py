"""Integration tests for inherited lifecycle behavior (Task 10)."""

from __future__ import annotations

import time

import pytest
import redis as redis_lib

from gpdatacached.config import GPDCDomainConfig
from gpdatacached.domain import GPDCDomain
from gpdatacached.keys import make_object_key, make_raw_key, make_ref_key

DOMAIN = "testdomain_inherit"
NS = "testns_inherit"
REDIS_HOST = "127.0.0.1"
REDIS_PORT = 6379
REDIS_DB = 1
REDIS_PASSWORD = "123456"

ALL_DOMAINS = [DOMAIN, DOMAIN + "_slide", DOMAIN + "_ttl", DOMAIN + "_ttl2"]


def _cleanup_domain(client: redis_lib.Redis, domain_name: str) -> None:
    pattern = f"gpdc:{domain_name}:*"
    cursor = 0
    while True:
        cursor, keys = client.scan(cursor=cursor, match=pattern, count=100)
        if keys:
            client.delete(*keys)
        if cursor == 0:
            break
    client.delete(f"gpdc:{domain_name}:__meta__")
    client.srem("gpdc:__domains__", domain_name)


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
    for domain_name in ALL_DOMAINS:
        _cleanup_domain(client, domain_name)
    client.close()


@pytest.fixture()
def ns(r):
    config = GPDCDomainConfig(
        redis_host=REDIS_HOST,
        redis_port=REDIS_PORT,
        redis_db=REDIS_DB,
        redis_password=REDIS_PASSWORD,
        gc_enabled=False,
    )
    domain = GPDCDomain(DOMAIN, config)
    return domain.get_namespace(NS)


def _make_namespace(r, domain_name: str, cache_mode: str = "permanent", ttl: int = 0):
    config = GPDCDomainConfig(
        redis_host=REDIS_HOST,
        redis_port=REDIS_PORT,
        redis_db=REDIS_DB,
        redis_password=REDIS_PASSWORD,
        gc_enabled=False,
        default_cache_mode=cache_mode,
        default_ttl_seconds=ttl,
    )
    domain = GPDCDomain(domain_name, config)
    return domain.get_namespace(NS)


def _extract_anon_name_from_dict(r, domain: str, ns: str, parent: str, field: str) -> str:
    raw_data = r.hgetall(make_raw_key(domain, ns, parent))
    encoded_val = raw_data.get(f"str:{field}", "")
    assert encoded_val.startswith("ref:gpdc:"), f"Expected ref, got: {encoded_val}"
    return encoded_val.split(":obj:")[1]


class TestAnonymousInherited:
    def test_anon_object_has_inherited_cache_mode(self, ns, r):
        ns["d"] = {"a": [1, 2, 3]}
        anon_name = _extract_anon_name_from_dict(r, DOMAIN, NS, "d", "a")
        anon_obj_key = make_object_key(DOMAIN, NS, anon_name)
        assert r.hget(anon_obj_key, "cache_mode") == "inherited"

    def test_anon_raw_key_has_ttl(self, r):
        ns = _make_namespace(r, DOMAIN, cache_mode="ttl", ttl=300)
        ns["d"] = {"a": [1, 2, 3]}
        anon_name = _extract_anon_name_from_dict(r, DOMAIN, NS, "d", "a")
        anon_raw_key = make_raw_key(DOMAIN, NS, anon_name)
        ttl_val = r.ttl(anon_raw_key)
        assert ttl_val > 0

    def test_anon_ref_key_has_ttl(self, r):
        ns = _make_namespace(r, DOMAIN, cache_mode="ttl", ttl=300)
        ns["d"] = {"a": [1, 2, 3]}
        anon_name = _extract_anon_name_from_dict(r, DOMAIN, NS, "d", "a")
        anon_ref_key = make_ref_key(DOMAIN, NS, anon_name)
        ttl_val = r.ttl(anon_ref_key)
        assert ttl_val > 0


class TestSlidingNestedContainer:
    def test_sliding_parent_refreshes_nested_anon_ttl(self, r):
        ns = _make_namespace(r, DOMAIN + "_slide", cache_mode="sliding", ttl=10)
        ns["d"] = {"items": [1, 2, 3]}
        anon_name = _extract_anon_name_from_dict(r, DOMAIN + "_slide", NS, "d", "items")
        anon_raw_key = make_raw_key(DOMAIN + "_slide", NS, anon_name)
        initial_ttl = r.ttl(anon_raw_key)
        assert initial_ttl > 0

        time.sleep(1)

        d = ns["d"]
        d["b"] = "hello"

        refreshed_ttl = r.ttl(anon_raw_key)
        assert refreshed_ttl > initial_ttl - 1


class TestTTLExpiryConsistency:
    def test_ttl_container_obj_has_ttl(self, r):
        ns = _make_namespace(r, DOMAIN + "_ttl", cache_mode="ttl", ttl=300)
        ns["lst"] = [1, 2, 3]
        obj_key = make_object_key(DOMAIN + "_ttl", NS, "lst")
        assert r.ttl(obj_key) > 0

    def test_ttl_container_with_nested_has_ttl_on_all_keys(self, r):
        ns = _make_namespace(r, DOMAIN + "_ttl2", cache_mode="ttl", ttl=300)
        ns["d"] = {"nested": [10, 20], "x": 1}
        anon_name = _extract_anon_name_from_dict(r, DOMAIN + "_ttl2", NS, "d", "nested")
        parent_obj_key = make_object_key(DOMAIN + "_ttl2", NS, "d")
        assert r.ttl(parent_obj_key) > 0

        anon_obj_key = make_object_key(DOMAIN + "_ttl2", NS, anon_name)
        anon_raw_key = make_raw_key(DOMAIN + "_ttl2", NS, anon_name)
        anon_ref_key = make_ref_key(DOMAIN + "_ttl2", NS, anon_name)
        assert r.ttl(anon_obj_key) > 0
        assert r.ttl(anon_raw_key) > 0
        assert r.ttl(anon_ref_key) > 0


class TestInheritedOverride:
    def test_set_cache_mode_detaches_from_inherited(self, ns, r):
        ns["d"] = {"a": [1, 2, 3]}
        d = ns["d"]
        inner = d["a"]
        assert inner.cache_mode == "inherited"

        inner.cache_mode = "ttl"

        assert inner.cache_mode == "ttl"

    def test_set_ttl_detaches_from_inherited(self, ns, r):
        ns["d"] = {"a": [1, 2, 3]}
        anon_name = _extract_anon_name_from_dict(r, DOMAIN, NS, "d", "a")
        d = ns["d"]
        inner = d["a"]

        inner.ttl = 600

        assert inner.ttl == 600
        obj_key = make_object_key(DOMAIN, NS, anon_name)
        assert r.hget(obj_key, "ttl") == "600"


class TestPermanentContainerLifecycle:
    def test_permanent_container_raw_is_persistent(self, ns, r):
        ns["lst"] = [1, 2, 3]
        raw_key = make_raw_key(DOMAIN, NS, "lst")
        assert r.ttl(raw_key) == -1
