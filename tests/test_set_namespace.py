"""Tests for namespace-level set operations (Phase 4)."""

from __future__ import annotations

import pytest
import redis as redis_lib

from gpdatacached.config import GPDCDomainConfig
from gpdatacached.domain import GPDCDomain
from gpdatacached.keys import make_object_key, make_raw_key, make_ref_key
from gpdatacached.objects import SetObject

REDIS_HOST = "127.0.0.1"
REDIS_PORT = 6379
REDIS_DB = 1
REDIS_PASSWORD = "123456"

DOMAIN = "testdomain_setns"
NS = "testns_setns"


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


class TestNamespaceSetCreate:
    def test_assign_set_creates_object(self, ns, r):
        ns["primes"] = {2, 3, 5, 7}
        obj_key = make_object_key(DOMAIN, NS, "primes")
        assert r.hget(obj_key, "type") == "collection:set"

    def test_assign_set_creates_raw(self, ns, r):
        ns["primes"] = {2, 3, 5}
        raw_key = make_raw_key(DOMAIN, NS, "primes")
        members = r.smembers(raw_key)
        assert members == {"int:2", "int:3", "int:5"}

    def test_assign_set_returns_setobject(self, ns):
        ns["primes"] = {2, 3, 5}
        obj = ns["primes"]
        assert isinstance(obj, SetObject)

    def test_assign_set_correct_value(self, ns):
        ns["primes"] = {2, 3, 5}
        obj = ns["primes"]
        assert set(obj) == {2, 3, 5}

    def test_assign_empty_set(self, ns, r):
        ns["empty"] = set()
        obj_key = make_object_key(DOMAIN, NS, "empty")
        assert r.hget(obj_key, "type") == "collection:set"
        obj = ns["empty"]
        assert len(obj) == 0

    def test_assign_set_with_tuples(self, ns):
        ns["tset"] = {1, (2, 3)}
        obj = ns["tset"]
        assert 1 in obj
        assert (2, 3) in obj


class TestNamespaceSetDelete:
    def test_del_set_removes_obj_key(self, ns, r):
        ns["primes"] = {2, 3, 5}
        obj_key = make_object_key(DOMAIN, NS, "primes")
        assert r.exists(obj_key)
        del ns["primes"]
        assert not r.exists(obj_key)

    def test_del_set_removes_raw_key(self, ns, r):
        ns["primes"] = {2, 3, 5}
        raw_key = make_raw_key(DOMAIN, NS, "primes")
        assert r.exists(raw_key)
        del ns["primes"]
        assert not r.exists(raw_key)

    def test_del_set_removes_ref_key(self, ns, r):
        ns["primes"] = {2, 3, 5}
        ref_key = make_ref_key(DOMAIN, NS, "primes")
        del ns["primes"]
        assert not r.exists(ref_key)


class TestNamespaceSetReferenceSharing:
    def test_assign_setobject_shares_raw(self, ns, r):
        ns["a"] = {1, 2, 3}
        ns["b"] = ns["a"]
        obj_a_key = make_object_key(DOMAIN, NS, "a")
        obj_b = make_object_key(DOMAIN, NS, "b")
        assert r.type(obj_b) == "string"
        assert r.get(obj_b) == f"ref:{obj_a_key}"

    def test_ref_set_tracks_referrer(self, ns, r):
        ns["a"] = {1, 2, 3}
        ns["b"] = ns["a"]
        ref_key_a = make_ref_key(DOMAIN, NS, "a")
        assert "b" in r.smembers(ref_key_a)

    def test_del_referrer_preserves_raw(self, ns, r):
        ns["a"] = {1, 2, 3}
        ns["b"] = ns["a"]
        del ns["b"]
        raw_a = make_raw_key(DOMAIN, NS, "a")
        assert r.exists(raw_a)

    def test_reassign_referrer_cleans_old_ref(self, ns, r):
        ns["a"] = {1, 2, 3}
        ns["b"] = ns["a"]
        ns["b"] = {4, 5}
        ref_key_a = make_ref_key(DOMAIN, NS, "a")
        assert "b" not in r.smembers(ref_key_a)
