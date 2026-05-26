"""Tests for reference counting improvements (Phase 4, Section 5)."""

from __future__ import annotations

import pytest
import redis as redis_lib

from gpdatacached.config import GPDCDomainConfig
from gpdatacached.domain import GPDCDomain
from gpdatacached.exceptions import CanonicalObjectReferencedError
from gpdatacached.keys import make_object_key, make_raw_key, make_ref_key

REDIS_HOST = "127.0.0.1"
REDIS_PORT = 6379
REDIS_DB = 1
REDIS_PASSWORD = "123456"

DOMAIN = "testdomain_ref"
NS = "testns_ref"


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


class TestReassignmentRefCleanup:
    def test_overwrite_owner_list_with_scalar_when_no_refs(self, ns):
        ns["x"] = [1, 2, 3]
        ns["x"] = 42
        assert ns["x"] == 42

    def test_reassign_dict_to_scalar_cleans_ref(self, ns, r):
        ns["x"] = {"a": 1}
        ns["y"] = ns["x"]
        ref_x = make_ref_key(DOMAIN, NS, "x")
        assert "y" in r.smembers(ref_x)

        ns["y"] = "hello"
        assert "y" not in r.smembers(ref_x)

    def test_reassign_set_to_scalar_cleans_ref(self, ns, r):
        ns["x"] = {1, 2, 3}
        ns["y"] = ns["x"]
        ref_x = make_ref_key(DOMAIN, NS, "x")
        assert "y" in r.smembers(ref_x)

        ns["y"] = 99
        assert "y" not in r.smembers(ref_x)

    def test_reassign_alias_to_new_container_cleans_ref(self, ns, r):
        ns["a"] = [1, 2]
        ns["b"] = ns["a"]
        ns["c"] = [10, 20]
        ns["b"] = ns["c"]
        ref_a = make_ref_key(DOMAIN, NS, "a")
        ref_c = make_ref_key(DOMAIN, NS, "c")
        assert "b" not in r.smembers(ref_a)
        assert "b" in r.smembers(ref_c)

    def test_reassign_ref_to_existing_container_cleans_old_ref(self, ns, r):
        ns["x"] = [1, 2]
        ns["y"] = [3, 4]
        ns["z"] = ns["x"]

        ns["z"] = ns["y"]
        ref_x = make_ref_key(DOMAIN, NS, "x")
        ref_y = make_ref_key(DOMAIN, NS, "y")
        assert "z" not in r.smembers(ref_x)
        assert "z" in r.smembers(ref_y)


class TestMultipleRefs:
    def test_multiple_refs_to_same_container(self, ns, r):
        ns["x"] = [1, 2, 3]
        ns["y"] = ns["x"]
        ns["z"] = ns["x"]
        ref_x = make_ref_key(DOMAIN, NS, "x")
        assert r.smembers(ref_x) == {"y", "z"}

    def test_del_one_ref_preserves_data(self, ns, r):
        ns["x"] = [1, 2, 3]
        ns["y"] = ns["x"]
        ns["z"] = ns["x"]
        del ns["z"]
        raw_x = make_raw_key(DOMAIN, NS, "x")
        assert r.exists(raw_x)

    def test_del_all_refs_preserves_owner(self, ns, r):
        ns["x"] = [1, 2, 3]
        ns["y"] = ns["x"]
        del ns["y"]
        raw_x = make_raw_key(DOMAIN, NS, "x")
        obj_x = make_object_key(DOMAIN, NS, "x")
        assert r.exists(raw_x)
        assert r.exists(obj_x)

    def test_delete_owner_blocked_when_refs_exist(self, ns):
        ns["x"] = [1, 2]
        ns["y"] = ns["x"]
        with pytest.raises(CanonicalObjectReferencedError):
            del ns["x"]
        assert ns["x"].value == [1, 2]
        assert ns["y"].value == [1, 2]


class TestReassignToDifferentContainerType:
    def test_reassign_list_to_dict(self, ns, r):
        ns["x"] = [1, 2, 3]
        ns["y"] = ns["x"]
        ns["y"] = {"a": 1}
        ref_x = make_ref_key(DOMAIN, NS, "x")
        assert "y" not in r.smembers(ref_x)
        obj_y = make_object_key(DOMAIN, NS, "y")
        assert r.hget(obj_y, "type") == "collection:dict"

    def test_reassign_dict_to_set(self, ns, r):
        ns["x"] = {"a": 1}
        ns["y"] = ns["x"]
        ns["y"] = {1, 2}
        ref_x = make_ref_key(DOMAIN, NS, "x")
        assert "y" not in r.smembers(ref_x)
        obj_y = make_object_key(DOMAIN, NS, "y")
        assert r.hget(obj_y, "type") == "collection:set"

    def test_reassign_set_to_list(self, ns, r):
        ns["x"] = {1, 2}
        ns["y"] = ns["x"]
        ns["y"] = [3, 4]
        ref_x = make_ref_key(DOMAIN, NS, "x")
        assert "y" not in r.smembers(ref_x)
        obj_y = make_object_key(DOMAIN, NS, "y")
        assert r.hget(obj_y, "type") == "collection:list"
