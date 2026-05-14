"""Tests for namespace-level dict creation and operations."""

from __future__ import annotations

import pytest
import redis as redis_lib

from gpdatacached.config import GPDCDomainConfig
from gpdatacached.domain import GPDCDomain
from gpdatacached.keys import make_object_key, make_raw_key, make_ref_key
from gpdatacached.objects import DictObject

DOMAIN = "testdomain_dict"
NS = "testns_dict"
REDIS_HOST = "127.0.0.1"
REDIS_PORT = 6379
REDIS_DB = 1
REDIS_PASSWORD = "123456"


@pytest.fixture()
def r():
    client = redis_lib.Redis(
        host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, password=REDIS_PASSWORD, decode_responses=True
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
        redis_host=REDIS_HOST, redis_port=REDIS_PORT, redis_db=REDIS_DB, redis_password=REDIS_PASSWORD
    )
    domain = GPDCDomain(DOMAIN, config)
    return domain.get_namespace(NS)


class TestDictCreation:
    def test_setitem_dict_creates_obj_key(self, ns, r):
        ns["mydict"] = {"a": 1, "b": "hello"}
        obj_key = make_object_key(DOMAIN, NS, "mydict")
        assert r.exists(obj_key)
        assert r.hget(obj_key, "type") == "collection:dict"

    def test_setitem_dict_creates_raw_key(self, ns, r):
        ns["mydict"] = {"a": 1, "b": "hello"}
        raw_key = make_raw_key(DOMAIN, NS, "mydict")
        assert r.exists(raw_key)
        assert r.hlen(raw_key) == 2

    def test_getitem_dict_returns_dict_object(self, ns):
        ns["mydict"] = {"a": 1, "b": "hello"}
        result = ns["mydict"]
        assert isinstance(result, DictObject)

    def test_dict_read_write(self, ns):
        ns["mydict"] = {"name": "Alice", "age": 30}
        d = ns["mydict"]
        assert d["name"] == "Alice"
        assert d["age"] == 30

    def test_new_dict_has_empty_referrers(self, ns):
        ns["mydict"] = {"a": 1}
        obj = ns["mydict"]
        assert obj.referrers() == set()


class TestDictReferenceSharing:
    def test_assign_dict_object_shares_raw_key(self, ns, r):
        ns["a"] = {"x": 1}
        ns["b"] = ns["a"]
        obj_a_raw = r.hget(make_object_key(DOMAIN, NS, "a"), "value")
        obj_b_raw = r.hget(make_object_key(DOMAIN, NS, "b"), "value")
        assert obj_a_raw == obj_b_raw

    def test_assign_dict_object_adds_to_ref_set(self, ns, r):
        ns["a"] = {"x": 1}
        ns["b"] = ns["a"]
        ref_key = make_ref_key(DOMAIN, NS, "a")
        assert "b" in r.smembers(ref_key)

    def test_shared_dict_modification_visible(self, ns):
        ns["a"] = {"x": 1}
        ns["b"] = ns["a"]
        ns["b"]["y"] = 2
        assert ns["a"]["y"] == 2


class TestDictDeletion:
    def test_delete_no_referrers_removes_all_keys(self, ns, r):
        ns["d"] = {"x": 1}
        del ns["d"]
        assert not r.exists(make_object_key(DOMAIN, NS, "d"))
        assert not r.exists(make_raw_key(DOMAIN, NS, "d"))
        assert not r.exists(make_ref_key(DOMAIN, NS, "d"))

    def test_delete_with_referrers_keeps_raw(self, ns, r):
        ns["a"] = {"x": 1}
        ns["b"] = ns["a"]
        del ns["a"]
        assert not r.exists(make_object_key(DOMAIN, NS, "a"))
        assert r.exists(make_raw_key(DOMAIN, NS, "a"))

    def test_delete_nonexistent_raises(self, ns):
        with pytest.raises(KeyError):
            del ns["nonexistent"]


class TestDictReassignment:
    def test_reassign_dict_removes_old_ref(self, ns, r):
        ns["x"] = {"a": 1}
        ns["y"] = ns["x"]
        ref_x = make_ref_key(DOMAIN, NS, "x")
        assert "y" in r.smembers(ref_x)
        ns["y"] = {"b": 2}
        assert "y" not in r.smembers(ref_x)

    def test_reassign_scalar_to_dict(self, ns):
        ns["x"] = 42
        ns["x"] = {"a": 1}
        result = ns["x"]
        assert isinstance(result, DictObject)
        assert result["a"] == 1

    def test_reassign_dict_to_scalar(self, ns):
        ns["x"] = {"a": 1}
        ns["x"] = 42
        assert ns["x"] == 42


class TestMixedTypes:
    def test_scalar_list_dict_coexist(self, ns):
        ns["s"] = 42
        ns["l"] = [1, 2, 3]
        ns["d"] = {"a": 1}
        assert ns["s"] == 42
        d = ns["d"]
        assert isinstance(d, DictObject)
        assert d["a"] == 1
