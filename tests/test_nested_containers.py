"""Tests for anonymous objects and nested containers (Phase 3)."""

from __future__ import annotations

import pytest
import redis as redis_lib

from gpdatacached.config import GPDCDomainConfig
from gpdatacached.domain import GPDCDomain
from gpdatacached.keys import make_object_key, make_raw_key, make_ref_key
from gpdatacached.objects import DictObject, ListObject

DOMAIN = "testdomain_nest"
NS = "testns_nest"
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


class TestDictNestedList:
    def test_create_dict_with_nested_list(self, ns, r):
        ns["mydict"] = {"a": [1, 2, 3], "b": "hello"}
        obj_key = make_object_key(DOMAIN, NS, "mydict")
        assert r.hget(obj_key, "type") == "collection:dict"

    def test_nested_list_creates_anon_obj(self, ns, r):
        ns["mydict"] = {"a": [1, 2, 3], "b": "hello"}
        raw_data = r.hgetall(make_raw_key(DOMAIN, NS, "mydict"))
        encoded_a = raw_data.get("str:a", "")
        assert encoded_a.startswith("ref:gpdc:")

    def test_nested_list_has_correct_data(self, ns):
        ns["mydict"] = {"a": [1, 2, 3], "b": "hello"}
        d = ns["mydict"]
        inner_list = d["a"]
        assert isinstance(inner_list, ListObject)
        assert list(inner_list) == [1, 2, 3]

    def test_nested_scalar_accessible(self, ns):
        ns["mydict"] = {"a": [1, 2, 3], "b": "hello"}
        d = ns["mydict"]
        assert d["b"] == "hello"

    def test_anon_obj_ref_set_contains_parent(self, ns, r):
        ns["mydict"] = {"a": [1, 2, 3], "b": "hello"}
        raw_data = r.hgetall(make_raw_key(DOMAIN, NS, "mydict"))
        ref_str = raw_data["str:a"]
        anon_name = ref_str.split(":obj:")[1]
        ref_key = make_ref_key(DOMAIN, NS, anon_name)
        assert "mydict" in r.smembers(ref_key)

    def test_anon_not_in_namespace_iteration(self, ns):
        ns["mydict"] = {"a": [1, 2, 3]}
        names = list(ns)
        assert all(not n.startswith("~") for n in names)


class TestDictNestedDict:
    def test_create_dict_with_nested_dict(self, ns):
        ns["mydict"] = {"a": {"x": 1}, "b": "hello"}
        d = ns["mydict"]
        inner = d["a"]
        assert isinstance(inner, DictObject)
        assert inner["x"] == 1

    def test_nested_dict_has_own_raw_key(self, ns, r):
        ns["mydict"] = {"a": {"x": 1}}
        raw_data = r.hgetall(make_raw_key(DOMAIN, NS, "mydict"))
        ref_str = raw_data["str:a"]
        anon_name = ref_str.split(":obj:")[1]
        anon_raw_key = make_raw_key(DOMAIN, NS, anon_name)
        assert r.exists(anon_raw_key)
        assert r.hget(anon_raw_key, "str:x") == "int:1"


class TestListNestedDict:
    def test_create_list_with_nested_dict(self, ns):
        ns["mylist"] = [{"name": "Alice"}, {"name": "Bob"}]
        lst = ns["mylist"]
        assert isinstance(lst[0], DictObject)
        assert lst[0]["name"] == "Alice"
        assert isinstance(lst[1], DictObject)
        assert lst[1]["name"] == "Bob"


class TestDeepNesting:
    def test_dict_in_dict_in_dict(self, ns):
        ns["d"] = {"a": {"b": {"c": 42}}}
        d = ns["d"]
        assert d["a"]["b"]["c"] == 42

    def test_list_in_dict_in_list(self, ns):
        ns["x"] = [{"items": [1, 2, 3]}]
        lst = ns["x"]
        inner_dict = lst[0]
        assert isinstance(inner_dict, DictObject)
        inner_list = inner_dict["items"]
        assert isinstance(inner_list, ListObject)
        assert list(inner_list) == [1, 2, 3]


class TestCascadeDeletion:
    def test_delete_dict_cascades_to_anon(self, ns, r):
        ns["mydict"] = {"a": [1, 2, 3], "b": "hello"}
        raw_data = r.hgetall(make_raw_key(DOMAIN, NS, "mydict"))
        anon_name = raw_data["str:a"].split(":obj:")[1]
        del ns["mydict"]
        assert not r.exists(make_object_key(DOMAIN, NS, anon_name))
        assert not r.exists(make_raw_key(DOMAIN, NS, anon_name))

    def test_delete_shared_anon_preserves_if_referrers_exist(self, ns, r):
        ns["x"] = {"a": [1, 2, 3]}
        ns["y"] = ns["x"]
        raw_data = r.hgetall(make_raw_key(DOMAIN, NS, "x"))
        anon_name = raw_data["str:a"].split(":obj:")[1]
        del ns["x"]
        assert r.exists(make_object_key(DOMAIN, NS, anon_name))


class TestDictSetNested:
    def test_set_nested_list_in_existing_dict(self, ns):
        ns["d"] = {"a": 1}
        d = ns["d"]
        d["b"] = [4, 5, 6]
        inner = d["b"]
        assert isinstance(inner, ListObject)
        assert list(inner) == [4, 5, 6]

    def test_set_nested_dict_in_existing_dict(self, ns):
        ns["d"] = {"a": 1}
        d = ns["d"]
        d["b"] = {"x": 10}
        inner = d["b"]
        assert isinstance(inner, DictObject)
        assert inner["x"] == 10


class TestCopyDeepCopy:
    def test_shallow_copy_shares_anon_refs(self, ns):
        ns["original"] = {"a": [1, 2, 3], "b": "hello"}
        ns["shallow"] = ns["original"].copy()
        ns["shallow"]["a"].append(4)
        assert list(ns["original"]["a"]) == [1, 2, 3, 4]

    def test_deep_copy_creates_independent_anons(self, ns):
        ns["original"] = {"a": [1, 2, 3], "b": "hello"}
        ns["deep"] = ns["original"].deepcopy()
        ns["deep"]["a"].append(4)
        assert list(ns["original"]["a"]) == [1, 2, 3]


class TestSpecDataFlow:
    def test_full_data_flow_example(self, ns, r):
        """Spec §11 complete example."""
        ns["mydict"] = {"a": [1, 2, 3], "b": {"x": 1}, "c": 42}
        d = ns["mydict"]
        assert d["c"] == 42
        assert d["a"][0] == 1
        assert d["b"]["x"] == 1

    def test_add_nested_list_to_existing_dict(self, ns):
        ns["mydict"] = {"c": 42}
        d = ns["mydict"]
        d["d"] = [4, 5, 6]
        assert isinstance(d["d"], ListObject)
        assert list(d["d"]) == [4, 5, 6]

    def test_tuple_key_in_nested_dict(self, ns):
        ns["d"] = {"a": 1}
        d = ns["d"]
        d[("key1", "key2")] = "value"
        assert d[("key1", "key2")] == "value"

    def test_append_nested_to_list(self, ns):
        ns["mylist"] = [1, 2]
        lst = ns["mylist"]
        lst.append({"name": "Alice"})
        inner = lst[2]
        assert isinstance(inner, DictObject)
        assert inner["name"] == "Alice"

    def test_anon_cache_mode_inherits_parent(self, ns, r):
        """Spec §5.4: anonymous objects inherit parent's cache_mode."""
        ns["d"] = {"a": [1, 2, 3]}
        raw_data = r.hgetall(make_raw_key(DOMAIN, NS, "d"))
        anon_name = raw_data["str:a"].split(":obj:")[1]
        anon_obj_key = make_object_key(DOMAIN, NS, anon_name)
        parent_cache_mode = r.hget(make_object_key(DOMAIN, NS, "d"), "cache_mode")
        anon_cache_mode = r.hget(anon_obj_key, "cache_mode")
        assert anon_cache_mode == parent_cache_mode
