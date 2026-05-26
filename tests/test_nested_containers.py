"""Tests for anonymous objects and nested containers (Phase 3)."""

from __future__ import annotations

import pytest
import redis as redis_lib

from gpdatacached.config import GPDCDomainConfig
from gpdatacached.domain import GPDCDomain
from gpdatacached.keys import make_object_key, make_raw_key, make_ref_key
from gpdatacached.objects import DictObject, ListObject, SetObject

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

    def test_list_existing_container_slot_rejects_scalar(self, ns):
        ns["items"] = [0, {"a": 1}]
        lst = ns["items"]

        with pytest.raises(TypeError, match="Cannot overwrite an existing container slot"):
            lst[1] = 42

    def test_list_existing_container_slot_rejects_container(self, ns):
        ns["items"] = [0, {"a": 1}]
        lst = ns["items"]

        with pytest.raises(TypeError, match="Cannot overwrite an existing container slot"):
            lst[1] = [4, 5]

    def test_list_scalar_slot_can_upgrade_to_container_once(self, ns):
        ns["items"] = [1, 2, 3]
        lst = ns["items"]

        lst[1] = {"a": 1}

        assert lst[1]["a"] == 1

    def test_delete_container_slot_cascades_removed_child(self, ns, r):
        ns["items"] = [0, {"a": 1}]
        lst = ns["items"]
        anon_name = lst[1].name

        del lst[1]

        assert list(lst) == [0]
        assert not r.exists(make_ref_key(DOMAIN, NS, anon_name))
        assert not r.exists(make_object_key(DOMAIN, NS, anon_name))
        assert not r.exists(make_raw_key(DOMAIN, NS, anon_name))

    def test_pop_container_slot_cascades_removed_child(self, ns, r):
        ns["items"] = [0, {"a": 1}]
        lst = ns["items"]
        anon_name = lst[1].name

        popped = lst.pop()

        assert popped["a"] == 1
        assert list(lst) == [0]
        assert not r.exists(make_ref_key(DOMAIN, NS, anon_name))
        assert not r.exists(make_object_key(DOMAIN, NS, anon_name))
        assert not r.exists(make_raw_key(DOMAIN, NS, anon_name))

    def test_pop_list_item_by_index_cascades_to_anon(self, ns, r):
        ns["items"] = [{"name": "Alice"}, 2]
        lst = ns["items"]
        anon_name = lst[0].name

        popped = lst.pop(0)

        assert popped == {"name": "Alice"}
        assert list(lst) == [2]
        assert not r.exists(make_ref_key(DOMAIN, NS, anon_name))
        assert not r.exists(make_object_key(DOMAIN, NS, anon_name))
        assert not r.exists(make_raw_key(DOMAIN, NS, anon_name))

    def test_remove_list_item_cascades_to_anon(self, ns, r):
        ns["items"] = [{"name": "Alice"}, 2]
        lst = ns["items"]
        nested = lst[0]
        anon_name = nested.name

        lst.remove(nested)

        assert list(lst) == [2]
        assert not r.exists(make_ref_key(DOMAIN, NS, anon_name))
        assert not r.exists(make_object_key(DOMAIN, NS, anon_name))
        assert not r.exists(make_raw_key(DOMAIN, NS, anon_name))

    def test_clear_list_cascades_all_nested_anons(self, ns, r):
        ns["items"] = [{"name": "Alice"}, {"name": "Bob"}]
        lst = ns["items"]
        first = lst[0].name
        second = lst[1].name

        lst.clear()

        assert list(lst) == []
        assert not r.exists(make_object_key(DOMAIN, NS, first))
        assert not r.exists(make_raw_key(DOMAIN, NS, first))
        assert not r.exists(make_object_key(DOMAIN, NS, second))
        assert not r.exists(make_raw_key(DOMAIN, NS, second))

    def test_pop_list_item_via_shared_handle_cascades_to_anon(self, ns, r):
        ns["x"] = [{"name": "Alice"}, 2]
        ns["y"] = ns["x"]
        shared = ns["y"]
        anon_name = shared[0].name

        popped = shared.pop(0)

        assert popped == {"name": "Alice"}
        assert list(shared) == [2]
        assert not r.exists(make_ref_key(DOMAIN, NS, anon_name))
        assert not r.exists(make_object_key(DOMAIN, NS, anon_name))
        assert not r.exists(make_raw_key(DOMAIN, NS, anon_name))

    def test_clear_list_via_shared_handle_cascades_nested_anons(self, ns, r):
        ns["x"] = [{"name": "Alice"}, {"name": "Bob"}]
        ns["y"] = ns["x"]
        shared = ns["y"]
        first = shared[0].name
        second = shared[1].name

        shared.clear()

        assert list(shared) == []
        assert not r.exists(make_object_key(DOMAIN, NS, first))
        assert not r.exists(make_raw_key(DOMAIN, NS, first))
        assert not r.exists(make_object_key(DOMAIN, NS, second))
        assert not r.exists(make_raw_key(DOMAIN, NS, second))

    def test_delete_list_duplicate_nested_handle_preserves_remaining_slot(self, ns, r):
        ns["items"] = [{"x": 1}]
        lst = ns["items"]
        lst.append(lst[0])
        anon_name = lst[0].name

        del lst[0]

        assert len(lst) == 1
        assert lst[0]["x"] == 1
        assert r.exists(make_ref_key(DOMAIN, NS, anon_name))
        assert r.exists(make_object_key(DOMAIN, NS, anon_name))
        assert r.exists(make_raw_key(DOMAIN, NS, anon_name))

    def test_delete_then_recreate_list_container_slot_does_not_leak_removed_child(self, ns, r):
        ns["items"] = [0, {"a": 1}]
        lst = ns["items"]
        removed_anon = lst[1].name

        del lst[1]
        lst.insert(1, {"b": 2})

        assert lst[1]["b"] == 2
        assert not r.exists(make_ref_key(DOMAIN, NS, removed_anon))
        assert not r.exists(make_object_key(DOMAIN, NS, removed_anon))
        assert not r.exists(make_raw_key(DOMAIN, NS, removed_anon))


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

    @pytest.mark.skip(reason="Pending refactor")
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

    def test_pop_nested_dict_returns_materialized_value(self, ns, r):
        ns["d"] = {"nested": {"x": 1}}
        d = ns["d"]
        anon_name = d["nested"].name

        popped = d.pop("nested")

        assert popped == {"x": 1}
        assert "nested" not in d
        assert not r.exists(make_ref_key(DOMAIN, NS, anon_name))
        assert not r.exists(make_object_key(DOMAIN, NS, anon_name))
        assert not r.exists(make_raw_key(DOMAIN, NS, anon_name))

    def test_del_nested_dict_item_cascades_removed_child(self, ns, r):
        ns["d"] = {"nested": {"x": 1}, "other": 2}
        d = ns["d"]
        anon_name = d["nested"].name

        del d["nested"]

        assert "nested" not in d
        assert not r.exists(make_ref_key(DOMAIN, NS, anon_name))
        assert not r.exists(make_object_key(DOMAIN, NS, anon_name))
        assert not r.exists(make_raw_key(DOMAIN, NS, anon_name))

    def test_clear_dict_cascades_nested_anons(self, ns, r):
        ns["d"] = {"a": [1, 2, 3], "b": {"x": 1}}
        d = ns["d"]
        first = d["a"].name
        second = d["b"].name

        d.clear()

        assert dict(d.items()) == {}
        assert not r.exists(make_object_key(DOMAIN, NS, first))
        assert not r.exists(make_raw_key(DOMAIN, NS, first))
        assert not r.exists(make_object_key(DOMAIN, NS, second))
        assert not r.exists(make_raw_key(DOMAIN, NS, second))

    def test_del_nested_dict_item_via_shared_handle_cascades_removed_child(self, ns, r):
        ns["x"] = {"a": [1, 2, 3]}
        ns["y"] = ns["x"]
        shared = ns["y"]
        anon_name = shared["a"].name

        del shared["a"]

        assert "a" not in shared
        assert not r.exists(make_ref_key(DOMAIN, NS, anon_name))
        assert not r.exists(make_object_key(DOMAIN, NS, anon_name))
        assert not r.exists(make_raw_key(DOMAIN, NS, anon_name))

    def test_clear_dict_via_shared_handle_cascades_nested_anons(self, ns, r):
        ns["x"] = {"a": [1, 2, 3], "b": {"x": 1}}
        ns["y"] = ns["x"]
        shared = ns["y"]
        first = shared["a"].name
        second = shared["b"].name

        shared.clear()

        assert dict(shared.items()) == {}
        assert not r.exists(make_object_key(DOMAIN, NS, first))
        assert not r.exists(make_raw_key(DOMAIN, NS, first))
        assert not r.exists(make_object_key(DOMAIN, NS, second))
        assert not r.exists(make_raw_key(DOMAIN, NS, second))

    def test_delete_dict_duplicate_nested_handle_preserves_remaining_slot(self, ns, r):
        ns["d"] = {"a": {"x": 1}}
        d = ns["d"]
        d["b"] = d["a"]
        anon_name = d["a"].name

        del d["a"]

        assert d["b"]["x"] == 1
        assert r.exists(make_ref_key(DOMAIN, NS, anon_name))
        assert r.exists(make_object_key(DOMAIN, NS, anon_name))
        assert r.exists(make_raw_key(DOMAIN, NS, anon_name))

    def test_delete_named_container_nested_ref_preserves_named_child(self, ns):
        ns["child"] = {"x": 1}
        ns["parent"] = {}
        parent = ns["parent"]
        parent["nested"] = ns["child"]

        del parent["nested"]

        assert "child" in ns
        assert ns["child"]["x"] == 1

    def test_pop_named_container_nested_ref_preserves_named_child(self, ns):
        ns["child"] = {"x": 1}
        ns["parent"] = {}
        parent = ns["parent"]
        parent["nested"] = ns["child"]

        popped = parent.pop("nested")

        assert popped == {"x": 1}
        assert "child" in ns
        assert ns["child"]["x"] == 1

    def test_clear_named_container_nested_refs_preserves_named_children(self, ns):
        ns["child_a"] = {"x": 1}
        ns["child_b"] = {"y": 2}
        ns["parent"] = {}
        parent = ns["parent"]
        parent["a"] = ns["child_a"]
        parent["b"] = ns["child_b"]

        parent.clear()

        assert "child_a" in ns
        assert "child_b" in ns
        assert ns["child_a"]["x"] == 1
        assert ns["child_b"]["y"] == 2


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


class TestDictNestedSet:
    def test_create_dict_with_nested_set(self, ns, r):
        ns["mydict"] = {"nums": {1, 2, 3}, "label": "test"}
        obj_key = make_object_key(DOMAIN, NS, "mydict")
        assert r.hget(obj_key, "type") == "collection:dict"

    def test_nested_set_creates_anon_obj(self, ns, r):
        ns["mydict"] = {"nums": {1, 2, 3}, "label": "test"}
        raw_data = r.hgetall(make_raw_key(DOMAIN, NS, "mydict"))
        encoded_nums = raw_data.get("str:nums", "")
        assert encoded_nums.startswith("ref:gpdc:")

    def test_nested_set_has_correct_data(self, ns):
        ns["mydict"] = {"nums": {1, 2, 3}, "label": "test"}
        d = ns["mydict"]
        inner_set = d["nums"]
        assert isinstance(inner_set, SetObject)
        assert set(inner_set) == {1, 2, 3}

    def test_nested_set_anon_ref(self, ns, r):
        ns["mydict"] = {"nums": {1, 2, 3}}
        raw_data = r.hgetall(make_raw_key(DOMAIN, NS, "mydict"))
        ref_str = raw_data["str:nums"]
        anon_name = ref_str.split(":obj:")[1]
        ref_key = make_ref_key(DOMAIN, NS, anon_name)
        assert "mydict" in r.smembers(ref_key)


class TestListNestedSet:
    def test_create_list_with_nested_set(self, ns, r):
        ns["mylist"] = [1, {2, 3}, "hello"]
        obj_key = make_object_key(DOMAIN, NS, "mylist")
        assert r.hget(obj_key, "type") == "collection:list"

    def test_nested_set_has_correct_data(self, ns):
        ns["mylist"] = [1, {2, 3}, "hello"]
        lst = ns["mylist"]
        inner = lst[1]
        assert isinstance(inner, SetObject)
        assert set(inner) == {2, 3}
