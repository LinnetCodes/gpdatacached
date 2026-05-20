"""Tests for reference counting improvements (Phase 4, Section 5)."""

from __future__ import annotations

import pytest
import redis as redis_lib

from gpdatacached.config import GPDCDomainConfig
from gpdatacached.domain import GPDCDomain
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
    def test_reassign_owner_list_to_scalar_is_rejected(self, ns, r):
        ns["x"] = [1, 2, 3]
        ref_x = make_ref_key(DOMAIN, NS, "x")

        with pytest.raises(TypeError, match="Cannot reassign a namespace container owner"):
            ns["x"] = 42

        assert list(ns["x"]) == [1, 2, 3]
        assert r.smembers(ref_x) == set()

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

    def test_reassign_ref_to_new_container_cleans_ref(self, ns, r):
        ns["x"] = [1, 2, 3]
        ns["y"] = ns["x"]
        ns["y"] = [4, 5, 6]
        ref_x = make_ref_key(DOMAIN, NS, "x")
        assert "y" not in r.smembers(ref_x)
        assert list(ns["x"]) == [1, 2, 3]
        assert list(ns["y"]) == [4, 5, 6]

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

    def test_recreating_deleted_owner_name_does_not_reuse_shared_raw_data(self, ns, r):
        ns["x"] = [1, 2, 3]
        ns["y"] = ns["x"]

        del ns["x"]
        ns["x"] = ["new"]

        assert list(ns["x"]) == ["new"]
        assert list(ns["y"]) == [1, 2, 3]
        assert r.hget(make_object_key(DOMAIN, NS, "x"), "value") != r.hget(make_object_key(DOMAIN, NS, "y"), "value")

    def test_rebinding_last_referrer_after_owner_delete_cleans_hidden_owner(self, ns, r):
        ns["a"] = [{"nested": [1, 2, 3]}]
        ns["b"] = ns["a"]

        del ns["a"]

        hidden_raw_key = r.hget(make_object_key(DOMAIN, NS, "b"), "value")
        hidden_obj_key = r.lindex(hidden_raw_key, 0)[len("ref:") :]
        hidden_name = hidden_obj_key.split(":obj:", 1)[1]
        hidden_dict_raw_key = r.hget(hidden_obj_key, "value")
        nested_obj_key = r.hget(hidden_dict_raw_key, "str:nested")[len("ref:") :]
        nested_raw_key = r.hget(nested_obj_key, "value")

        ns["b"] = 42

        assert ns["b"] == 42
        assert not r.exists(hidden_obj_key)
        assert not r.exists(hidden_dict_raw_key)
        assert not r.exists(make_ref_key(DOMAIN, NS, hidden_name))
        assert not r.exists(nested_obj_key)
        assert not r.exists(nested_raw_key)

    def test_rebinding_last_referrer_after_rehomed_append_cleans_nested_anons(self, ns, r):
        ns["a"] = []
        ns["b"] = ns["a"]
        ns["b"].append({"nested": [1]})

        outer_obj = ns["b"][0]
        nested_obj = outer_obj["nested"]
        outer_name = outer_obj.name
        nested_name = nested_obj.name

        del ns["a"]
        ns["b"] = 42

        assert ns["b"] == 42
        assert not r.exists(make_object_key(DOMAIN, NS, outer_name))
        assert not r.exists(make_raw_key(DOMAIN, NS, outer_name))
        assert not r.exists(make_ref_key(DOMAIN, NS, outer_name))
        assert not r.exists(make_object_key(DOMAIN, NS, nested_name))
        assert not r.exists(make_raw_key(DOMAIN, NS, nested_name))
        assert not r.exists(make_ref_key(DOMAIN, NS, nested_name))

    def test_append_after_owner_delete_binds_new_nested_anons_to_hidden_owner(self, ns, r):
        ns["a"] = []
        ns["b"] = ns["a"]

        del ns["a"]
        ns["b"].append({"nested": [1]})

        outer_obj = ns["b"][0]
        nested_obj = outer_obj["nested"]
        outer_name = outer_obj.name
        nested_name = nested_obj.name

        ns["b"] = 42

        assert ns["b"] == 42
        assert not r.exists(make_object_key(DOMAIN, NS, outer_name))
        assert not r.exists(make_raw_key(DOMAIN, NS, outer_name))
        assert not r.exists(make_ref_key(DOMAIN, NS, outer_name))
        assert not r.exists(make_object_key(DOMAIN, NS, nested_name))
        assert not r.exists(make_raw_key(DOMAIN, NS, nested_name))
        assert not r.exists(make_ref_key(DOMAIN, NS, nested_name))

    def test_self_rebind_of_last_hidden_owner_ref_preserves_data(self, ns):
        ns["x"] = [1, 2, 3]
        ns["y"] = ns["x"]

        del ns["x"]
        ns["y"] = ns["y"]

        assert list(ns["y"]) == [1, 2, 3]

    def test_stale_handle_rebind_after_owner_delete_preserves_data(self, ns):
        ns["a"] = [1, 2, 3]
        ns["b"] = ns["a"]
        handle = ns["b"]

        del ns["a"]
        ns["b"] = handle

        assert list(ns["b"]) == [1, 2, 3]

    def test_stale_handle_nested_ref_after_owner_delete_uses_hidden_owner(self, ns):
        ns["a"] = [1, 2, 3]
        ns["b"] = ns["a"]
        handle = ns["b"]
        ns["parent"] = {}

        del ns["a"]
        ns["parent"]["nested"] = handle

        assert list(ns["b"]) == [1, 2, 3]
        assert list(ns["parent"]["nested"]) == [1, 2, 3]


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
