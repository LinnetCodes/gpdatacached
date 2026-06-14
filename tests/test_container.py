from __future__ import annotations

import collections.abc
import datetime
import time

import pytest

from gpdatacached import DictObject, ListObject, SetObject, TupleObject
from gpdatacached import DictCodec, ListCodec, SetCodec
from gpdatacached.keys import make_object_key, make_raw_key, make_ref_key

DOMAIN = "testdomain"
NS = "testns"


@pytest.fixture
def setup_list(raw_redis):
    raw_redis.flushdb()

    obj_key = make_object_key(DOMAIN, NS, "mylist")
    raw_key = make_raw_key(DOMAIN, NS, "mylist")
    ref_key = make_ref_key(DOMAIN, NS, "mylist")

    now = datetime.datetime.now(datetime.UTC).isoformat()
    raw_redis.hset(
        obj_key,
        mapping={
            "name": "mylist",
            "type": "collection:list",
            "cache_mode": "permanent",
            "ttl": "0",
            "created_at": now,
            "updated_at": now,
        },
    )

    class FakeDomain:
        name = DOMAIN
        redis = raw_redis

    class FakeNamespace:
        name = NS
        domain = FakeDomain()

        def _redis(self):
            return raw_redis

    ns = FakeNamespace()

    ListCodec.create_raw(ns, raw_key, [10, 20, 30], "mylist", "permanent", 0)

    obj = ListObject("mylist", ns)
    return obj, raw_redis, obj_key, raw_key, ref_key, ns


@pytest.fixture
def setup_dict(raw_redis):
    raw_redis.flushdb()

    obj_key = make_object_key(DOMAIN, NS, "mydict")
    raw_key = make_raw_key(DOMAIN, NS, "mydict")
    ref_key = make_ref_key(DOMAIN, NS, "mydict")

    now = datetime.datetime.now(datetime.UTC).isoformat()
    raw_redis.hset(
        obj_key,
        mapping={
            "type": "collection:dict",
            "value": raw_key,
            "cache_mode": "permanent",
            "ttl": "",
            "created_at": now,
            "updated_at": now,
        },
    )

    class FakeDomain:
        name = DOMAIN
        redis = raw_redis

    class FakeNamespace:
        name = NS
        domain = FakeDomain()

        def _redis(self):
            return raw_redis

        def _cascade_delete_anonymous(self, anon_name):
            obj_key = make_object_key(DOMAIN, NS, anon_name)
            meta = raw_redis.hgetall(obj_key)
            if not meta:
                return

            raw_key = meta.get("value", "")
            ref_key = make_ref_key(DOMAIN, NS, anon_name)

            if raw_key:
                raw_redis.delete(raw_key)
            raw_redis.delete(ref_key)
            raw_redis.delete(obj_key)

    ns = FakeNamespace()

    DictCodec.delete_raw(ns, raw_key)
    DictCodec.create_raw(ns, raw_key, {"name": "Alice", "age": 30}, "mydict", "permanent", 0)

    obj = DictObject("mydict", ns)
    return obj, raw_redis, obj_key, raw_key, ref_key


@pytest.fixture
def setup_set(raw_redis):
    raw_redis.flushdb()

    obj_key = make_object_key(DOMAIN, NS, "myset")
    raw_key = make_raw_key(DOMAIN, NS, "myset")
    ref_key = make_ref_key(DOMAIN, NS, "myset")

    now = datetime.datetime.now(datetime.UTC).isoformat()
    raw_redis.hset(
        obj_key,
        mapping={
            "type": "collection:set",
            "value": raw_key,
            "cache_mode": "permanent",
            "ttl": "",
            "created_at": now,
            "updated_at": now,
        },
    )

    class FakeDomain:
        name = DOMAIN
        redis = raw_redis

    class FakeNamespace:
        name = NS
        domain = FakeDomain()

        def _redis(self):
            return raw_redis

    ns = FakeNamespace()

    SetCodec.delete_raw(ns, raw_key)
    SetCodec.create_raw(ns, raw_key, {1, 2, 3}, "myset", "permanent", 0)

    obj = SetObject("myset", ns)
    return obj, raw_redis, obj_key, raw_key, ref_key


class TestListOps:
    def test_len(self, setup_list):
        obj, *_ = setup_list
        assert len(obj) == 3

    def test_getitem_positive(self, setup_list):
        obj, *_ = setup_list
        assert obj[0] == 10
        assert obj[1] == 20
        assert obj[2] == 30

    def test_getitem_negative(self, setup_list):
        obj, *_ = setup_list
        assert obj[-1] == 30
        assert obj[-2] == 20

    def test_getitem_slice_raises_typeerror(self, setup_list):
        obj, *_ = setup_list
        with pytest.raises(TypeError, match="slice indexing"):
            obj[1:3]

    def test_setitem(self, setup_list):
        obj, *_ = setup_list
        obj[1] = 99
        assert obj[1] == 99
        assert len(obj) == 3

    def test_setitem_slice_raises_typeerror(self, setup_list):
        obj, *_ = setup_list
        with pytest.raises(TypeError, match="slice indexing"):
            obj[1:3] = [99, 100]

    def test_delitem(self, setup_list):
        obj, *_ = setup_list
        del obj[1]
        assert len(obj) == 2
        assert list(obj) == [10, 30]

    def test_delitem_slice_raises_typeerror(self, setup_list):
        obj, *_ = setup_list
        with pytest.raises(TypeError, match="slice indexing"):
            del obj[1:3]

    def test_delitem_first(self, setup_list):
        obj, *_ = setup_list
        del obj[0]
        assert list(obj) == [20, 30]

    def test_delitem_last(self, setup_list):
        obj, *_ = setup_list
        del obj[-1]
        assert list(obj) == [10, 20]

    def test_append(self, setup_list):
        obj, *_ = setup_list
        obj.append(40)
        assert len(obj) == 4
        assert obj[-1] == 40

    def test_extend(self, setup_list):
        obj, *_ = setup_list
        obj.extend([40, 50])
        assert len(obj) == 5
        assert list(obj)[-2:] == [40, 50]

    def test_insert_at_start(self, setup_list):
        obj, *_ = setup_list
        obj.insert(0, 5)
        assert obj[0] == 5
        assert list(obj) == [5, 10, 20, 30]

    def test_insert_in_middle(self, setup_list):
        obj, *_ = setup_list
        obj.insert(2, 15)
        assert list(obj) == [10, 20, 15, 30]

    def test_pop_last(self, setup_list):
        obj, *_ = setup_list
        val = obj.pop()
        assert val == 30
        assert len(obj) == 2

    def test_pop_first(self, setup_list):
        obj, *_ = setup_list
        val = obj.pop(0)
        assert val == 10
        assert list(obj) == [20, 30]

    def test_remove(self, setup_list):
        obj, *_ = setup_list
        obj.append(10)
        obj.remove(10)
        assert list(obj) == [20, 30, 10]

    def test_reverse(self, setup_list):
        obj, *_ = setup_list
        obj.reverse()
        assert list(obj) == [30, 20, 10]

    def test_sort(self, setup_list):
        obj, r, obj_key, raw_key, ref_key, ns = setup_list
        ListCodec.delete_raw(ns, raw_key)
        ListCodec.create_raw(ns, raw_key, [30, 10, 20], "mylist", "permanent", 0)
        obj.sort()
        assert list(obj) == [10, 20, 30]

    def test_count(self, setup_list):
        obj, *_ = setup_list
        obj.append(10)
        assert obj.count(10) == 2
        assert obj.count(99) == 0

    def test_index(self, setup_list):
        obj, *_ = setup_list
        assert obj.index(20) == 1

    def test_contains(self, setup_list):
        obj, *_ = setup_list
        assert 20 in obj
        assert 99 not in obj

    def test_iteration(self, setup_list):
        obj, *_ = setup_list
        assert list(obj) == [10, 20, 30]

    def test_clear(self, setup_list):
        obj, *_ = setup_list
        obj.clear()
        assert len(obj) == 0
        assert list(obj) == []

    def test_value_getter(self, setup_list):
        obj, *_ = setup_list
        assert obj.value == [10, 20, 30]

    def test_value_setter_rejected(self, setup_list):
        obj, *_ = setup_list

        with pytest.raises(
            TypeError,
            match="Container value replacement is not supported; use explicit mutating methods instead",
        ):
            obj.value = [1, 2, 3]

        assert obj.value == [10, 20, 30]

    def test_referrers_empty(self, setup_list):
        obj, *_ = setup_list
        assert obj.referrers() == set()

    def test_referrers_with_data(self, setup_list):
        obj, r, obj_key, raw_key, ref_key, ns = setup_list
        r.hset(ref_key, "other_obj", "1")
        result = obj.referrers()
        assert result == {"other_obj"}

    def test_copy_returns_list(self, setup_list):
        obj, *_ = setup_list
        result = obj.copy()
        assert result == [10, 20, 30]
        assert isinstance(result, list)

    def test_deepcopy_returns_list(self, setup_list):
        obj, *_ = setup_list
        result = obj.deepcopy()
        assert result == [10, 20, 30]
        assert isinstance(result, list)

    def test_write_updates_updated_at(self, setup_list):
        obj, r, obj_key, *_ = setup_list
        before = r.hget(obj_key, "updated_at")
        time.sleep(0.01)
        obj.append(99)
        after = r.hget(obj_key, "updated_at")
        assert after >= before

    def test_type(self, setup_list):
        obj, *_ = setup_list
        assert obj.type == "collection:list"

    def test_sort_reverse(self, setup_list):
        obj, r, obj_key, raw_key, ref_key, ns = setup_list
        ListCodec.delete_raw(ns, raw_key)
        ListCodec.create_raw(ns, raw_key, [10, 30, 20], "mylist", "permanent", 0)
        obj.sort(reverse=True)
        assert list(obj) == [30, 20, 10]

    def test_sort_with_key(self, setup_list):
        obj, r, obj_key, raw_key, ref_key, ns = setup_list
        ListCodec.delete_raw(ns, raw_key)
        ListCodec.create_raw(ns, raw_key, [10, 30, 20], "mylist", "permanent", 0)
        obj.sort(key=lambda x: -x)
        assert list(obj) == [30, 20, 10]

    def test_sort_with_key_and_reverse(self, setup_list):
        obj, r, obj_key, raw_key, ref_key, ns = setup_list
        ListCodec.delete_raw(ns, raw_key)
        ListCodec.create_raw(ns, raw_key, [10, 30, 20], "mylist", "permanent", 0)
        obj.sort(key=lambda x: -x, reverse=True)
        assert list(obj) == [10, 20, 30]


class TestDictOps:
    def test_type(self, setup_dict):
        obj, *_ = setup_dict
        assert obj.type == "collection:dict"

    def test_len(self, setup_dict):
        obj, *_ = setup_dict
        assert len(obj) == 2

    def test_getitem_str_key(self, setup_dict):
        obj, *_ = setup_dict
        assert obj["name"] == "Alice"

    def test_getitem_int_key(self, setup_dict):
        obj, *_ = setup_dict
        assert obj["age"] == 30

    def test_getitem_missing_raises_key_error(self, setup_dict):
        obj, *_ = setup_dict
        with pytest.raises(KeyError):
            obj["nonexistent"]

    def test_setitem_overwrite(self, setup_dict):
        obj, *_ = setup_dict
        obj["name"] = "Bob"
        assert obj["name"] == "Bob"

    def test_setitem_new_key(self, setup_dict):
        obj, *_ = setup_dict
        obj["city"] = "NYC"
        assert obj["city"] == "NYC"
        assert len(obj) == 3

    def test_setitem_existing_container_slot_overwrite_scalar(self, setup_dict):
        obj, *_ = setup_dict
        obj["nested"] = [1, 2, 3]

        obj["nested"] = 1

        assert obj["nested"] == 1

    def test_setitem_existing_container_slot_overwrite_container(self, setup_dict):
        obj, *_ = setup_dict
        obj["nested"] = [1, 2, 3]

        obj["nested"] = {"x": 1}

        assert obj["nested"]["x"] == 1

    def test_delete_then_recreate_container_slot_is_allowed(self, setup_dict):
        obj, *_ = setup_dict
        obj["nested"] = [1, 2, 3]
        del obj["nested"]

        obj["nested"] = {"x": 1}

        assert obj["nested"]["x"] == 1

    def test_delitem(self, setup_dict):
        obj, *_ = setup_dict
        del obj["age"]
        assert len(obj) == 1
        assert "age" not in obj

    def test_delitem_missing_raises_key_error(self, setup_dict):
        obj, *_ = setup_dict
        with pytest.raises(KeyError):
            del obj["nonexistent"]

    def test_contains_true(self, setup_dict):
        obj, *_ = setup_dict
        assert "name" in obj

    def test_contains_false(self, setup_dict):
        obj, *_ = setup_dict
        assert "nonexistent" not in obj

    def test_iter(self, setup_dict):
        obj, *_ = setup_dict
        keys = set(obj)
        assert keys == {"name", "age"}

    def test_keys(self, setup_dict):
        obj, *_ = setup_dict
        result = obj.keys()
        assert isinstance(result, collections.abc.KeysView)
        assert set(result) == {"name", "age"}

    def test_values(self, setup_dict):
        obj, *_ = setup_dict
        result = obj.values()
        assert isinstance(result, collections.abc.ValuesView)
        assert set(result) == {"Alice", 30}

    def test_items(self, setup_dict):
        obj, *_ = setup_dict
        result = obj.items()
        assert isinstance(result, collections.abc.ItemsView)
        assert dict(result) == {"name": "Alice", "age": 30}

    def test_keys_values_items_are_snapshots(self, setup_dict):
        obj, *_ = setup_dict
        keys = obj.keys()
        values = obj.values()
        items = obj.items()
        obj["newkey"] = "newval"
        assert "newkey" not in keys
        assert "newval" not in values
        assert ("newkey", "newval") not in items

    def test_get_existing(self, setup_dict):
        obj, *_ = setup_dict
        assert obj.get("name") == "Alice"

    def test_get_missing_default(self, setup_dict):
        obj, *_ = setup_dict
        assert obj.get("missing", "fallback") == "fallback"

    def test_get_missing_no_default(self, setup_dict):
        obj, *_ = setup_dict
        assert obj.get("missing") is None

    def test_pop_existing(self, setup_dict):
        obj, *_ = setup_dict
        val = obj.pop("age")
        assert val == 30
        assert "age" not in obj

    def test_pop_missing_with_default(self, setup_dict):
        obj, *_ = setup_dict
        val = obj.pop("missing", 99)
        assert val == 99

    def test_pop_missing_raises(self, setup_dict):
        obj, *_ = setup_dict
        with pytest.raises(KeyError):
            obj.pop("missing")

    def test_setdefault_existing(self, setup_dict):
        obj, *_ = setup_dict
        val = obj.setdefault("name", "Default")
        assert val == "Alice"

    def test_setdefault_new(self, setup_dict):
        obj, *_ = setup_dict
        val = obj.setdefault("country", "US")
        assert val == "US"
        assert obj["country"] == "US"

    def test_update(self, setup_dict):
        obj, *_ = setup_dict
        obj.update({"age": 31, "city": "LA"})
        assert obj["age"] == 31
        assert obj["city"] == "LA"

    def test_clear(self, setup_dict):
        obj, *_ = setup_dict
        obj.clear()
        assert len(obj) == 0
        assert dict(obj.items()) == {}

    def test_value_getter(self, setup_dict):
        obj, *_ = setup_dict
        assert obj.value == {"name": "Alice", "age": 30}

    def test_value_setter_rejected(self, setup_dict):
        obj, *_ = setup_dict

        with pytest.raises(
            TypeError,
            match="Container value replacement is not supported; use explicit mutating methods instead",
        ):
            obj.value = {"x": 1, "y": 2}

        assert obj.value == {"name": "Alice", "age": 30}

    def test_tuple_key_setget(self, setup_dict):
        obj, *_ = setup_dict
        obj[("a", "b")] = "example"
        assert obj[("a", "b")] == "example"

    def test_tuple_key_contains(self, setup_dict):
        obj, *_ = setup_dict
        obj[("a", "b")] = "example"
        assert ("a", "b") in obj

    def test_tuple_key_delete(self, setup_dict):
        obj, *_ = setup_dict
        obj[("a", "b")] = "example"
        del obj[("a", "b")]
        assert ("a", "b") not in obj

    def test_write_updates_updated_at(self, setup_dict):
        obj, r, obj_key, *_ = setup_dict
        before = r.hget(obj_key, "updated_at")
        time.sleep(0.01)
        obj["newkey"] = "newval"
        after = r.hget(obj_key, "updated_at")
        assert after >= before

    def test_update_calls_touch_once(self, setup_dict):
        from unittest.mock import patch

        obj, *_ = setup_dict
        with patch.object(type(obj), "_touch", wraps=obj._touch) as mock_touch:
            obj.update({"a": 1, "b": 2, "c": 3})
            assert mock_touch.call_count == 1
        assert obj["a"] == 1
        assert obj["b"] == 2
        assert obj["c"] == 3

    def test_update_empty_dict_no_touch(self, setup_dict):
        from unittest.mock import patch

        obj, *_ = setup_dict
        with patch.object(type(obj), "_touch", wraps=obj._touch) as mock_touch:
            obj.update({})
            assert mock_touch.call_count == 0

    def test_update_kwargs_calls_touch_once(self, setup_dict):
        from unittest.mock import patch

        obj, *_ = setup_dict
        with patch.object(type(obj), "_touch", wraps=obj._touch) as mock_touch:
            obj.update(x=10, y=20)
            assert mock_touch.call_count == 1
        assert obj["x"] == 10
        assert obj["y"] == 20

    def test_update_item_count(self, setup_dict):
        obj, r, obj_key, *_ = setup_dict
        assert len(obj) == 2
        obj.update({"c": 3, "d": 4, "e": 5})
        assert len(obj) == 5
        item_count = r.hget(obj_key, "item_count")
        assert item_count == "5"

    def test_update_overwrites_existing(self, setup_dict):
        obj, *_ = setup_dict
        obj.update({"name": "Bob", "age": 99})
        assert obj["name"] == "Bob"
        assert obj["age"] == 99

    def test_update_iterable_pairs(self, setup_dict):
        obj, *_ = setup_dict
        obj.update([("city", "NYC"), ("zip", 10001)])
        assert obj["city"] == "NYC"
        assert obj["zip"] == 10001

    def test_update_iterable_pairs_touches_once(self, setup_dict):
        from unittest.mock import patch

        obj, *_ = setup_dict
        with patch.object(type(obj), "_touch", wraps=obj._touch) as mock_touch:
            obj.update([("p", 1), ("q", 2)])
            assert mock_touch.call_count == 1
        assert obj["p"] == 1
        assert obj["q"] == 2


class TestDictDiverseKeyTypes:
    def test_float_key_setget(self, ns):
        ns["d"] = {}
        d = ns.get_object("d")
        d[3.14] = "pi"
        assert d[3.14] == "pi"

    def test_float_key_contains(self, ns):
        ns["d"] = {}
        d = ns.get_object("d")
        d[3.14] = "pi"
        assert 3.14 in d

    def test_float_key_delete(self, ns):
        ns["d"] = {}
        d = ns.get_object("d")
        d[3.14] = "pi"
        del d[3.14]
        assert 3.14 not in d

    def test_bool_key_setget(self, ns):
        ns["d"] = {}
        d = ns.get_object("d")
        d[True] = "yes"
        d[False] = "no"
        assert d[True] == "yes"
        assert d[False] == "no"

    def test_bool_key_contains(self, ns):
        ns["d"] = {}
        d = ns.get_object("d")
        d[True] = "yes"
        assert True in d

    def test_none_key_setget(self, ns):
        ns["d"] = {}
        d = ns.get_object("d")
        d[None] = "nothing"
        assert d[None] == "nothing"

    def test_none_key_contains(self, ns):
        ns["d"] = {}
        d = ns.get_object("d")
        d[None] = "nothing"
        assert None in d

    def test_none_key_delete(self, ns):
        ns["d"] = {}
        d = ns.get_object("d")
        d[None] = "nothing"
        del d[None]
        assert None not in d

    def test_datetime_key_setget(self, ns):
        import datetime

        ns["d"] = {}
        d = ns.get_object("d")
        dt = datetime.datetime(2026, 1, 15, 12, 30, tzinfo=datetime.UTC)
        d[dt] = "event"
        assert d[dt] == "event"

    def test_datetime_key_contains(self, ns):
        import datetime

        ns["d"] = {}
        d = ns.get_object("d")
        dt = datetime.datetime(2026, 1, 15, 12, 30, tzinfo=datetime.UTC)
        d[dt] = "event"
        assert dt in d


def _make_set_obj(raw_redis, name, value):
    obj_key = make_object_key(DOMAIN, NS, name)
    raw_key = make_raw_key(DOMAIN, NS, name)
    now = datetime.datetime.now(datetime.UTC).isoformat()
    raw_redis.hset(
        obj_key,
        mapping={
            "type": "collection:set",
            "value": raw_key,
            "cache_mode": "permanent",
            "ttl": "",
            "created_at": now,
            "updated_at": now,
        },
    )

    class FakeDomain:
        name = DOMAIN
        redis = raw_redis

    class FakeNamespace:
        name = NS
        domain = FakeDomain()

        def _redis(self):
            return raw_redis

    ns = FakeNamespace()
    SetCodec.create_raw(ns, raw_key, value, name, "permanent", 0)

    return SetObject(name, ns)


class TestSetBasic:
    def test_type(self, setup_set):
        obj, *_ = setup_set
        assert obj.type == "collection:set"

    def test_len(self, setup_set):
        obj, *_ = setup_set
        assert len(obj) == 3

    def test_contains(self, setup_set):
        obj, *_ = setup_set
        assert 1 in obj
        assert 4 not in obj

    def test_iter(self, setup_set):
        obj, *_ = setup_set
        assert set(obj) == {1, 2, 3}

    def test_value_property(self, setup_set):
        obj, *_ = setup_set
        assert obj.value == {1, 2, 3}

    def test_value_setter_rejected(self, setup_set):
        obj, *_ = setup_set

        with pytest.raises(
            TypeError,
            match="Container value replacement is not supported; use explicit mutating methods instead",
        ):
            obj.value = {4, 5}

        assert obj.value == {1, 2, 3}


class TestSetMutableSetOps:
    def test_add(self, setup_set):
        obj, r, _, raw_key, _ = setup_set
        obj.add(4)
        assert 4 in obj
        assert len(obj) == 4

    def test_add_duplicate_is_noop(self, setup_set):
        obj, *_ = setup_set
        obj.add(1)
        assert len(obj) == 3

    def test_discard_existing(self, setup_set):
        obj, *_ = setup_set
        obj.discard(1)
        assert 1 not in obj
        assert len(obj) == 2

    def test_discard_nonexistent_is_noop(self, setup_set):
        obj, *_ = setup_set
        obj.discard(999)
        assert len(obj) == 3

    def test_remove_existing(self, setup_set):
        obj, *_ = setup_set
        obj.remove(1)
        assert 1 not in obj

    def test_remove_nonexistent_raises_keyerror(self, setup_set):
        obj, *_ = setup_set
        with pytest.raises(KeyError):
            obj.remove(999)

    def test_pop(self, setup_set):
        obj, *_ = setup_set
        popped = obj.pop()
        assert popped in {1, 2, 3}
        assert popped not in obj
        assert len(obj) == 2

    def test_pop_empty_set_raises_keyerror(self, raw_redis):
        raw_redis.flushdb()
        obj_key = make_object_key(DOMAIN, NS, "emptyset")
        raw_key = make_raw_key(DOMAIN, NS, "emptyset")
        now = datetime.datetime.now(datetime.UTC).isoformat()
        raw_redis.hset(
            obj_key,
            mapping={
                "type": "collection:set",
                "value": raw_key,
                "cache_mode": "permanent",
                "ttl": "",
                "created_at": now,
                "updated_at": now,
            },
        )

        class FakeDomain:
            name = DOMAIN
            redis = raw_redis

        class FakeNamespace:
            name = NS
            domain = FakeDomain()

        obj = SetObject("emptyset", FakeNamespace())
        with pytest.raises(KeyError):
            obj.pop()

    def test_clear(self, setup_set):
        obj, r, _, raw_key, _ = setup_set
        obj.clear()
        assert len(obj) == 0
        assert set(obj) == set()


class TestSetDeepCopy:
    def test_deepcopy_returns_python_set(self, setup_set):
        obj, *_ = setup_set
        result = obj.deepcopy()
        assert isinstance(result, set)
        assert result == {1, 2, 3}


class TestSetBinaryOps:
    def test_union_with_set(self, raw_redis):
        raw_redis.flushdb()
        obj = _make_set_obj(raw_redis, "s1", {1, 2, 3})
        result = obj | {3, 4}
        assert isinstance(result, set)
        assert result == {1, 2, 3, 4}

    def test_intersection_with_set(self, raw_redis):
        raw_redis.flushdb()
        obj = _make_set_obj(raw_redis, "s1", {1, 2, 3})
        result = obj & {2, 3, 4}
        assert isinstance(result, set)
        assert result == {2, 3}

    def test_difference_with_set(self, raw_redis):
        raw_redis.flushdb()
        obj = _make_set_obj(raw_redis, "s1", {1, 2, 3})
        result = obj - {2}
        assert isinstance(result, set)
        assert result == {1, 3}

    def test_symmetric_difference_with_set(self, raw_redis):
        raw_redis.flushdb()
        obj = _make_set_obj(raw_redis, "s1", {1, 2, 3})
        result = obj ^ {2, 3, 4}
        assert isinstance(result, set)
        assert result == {1, 4}

    def test_union_with_setobject(self, raw_redis):
        raw_redis.flushdb()
        s1 = _make_set_obj(raw_redis, "s1", {1, 2})
        s2 = _make_set_obj(raw_redis, "s2", {2, 3})
        result = s1 | s2
        assert isinstance(result, set)
        assert result == {1, 2, 3}


class TestSetInPlaceOps:
    def test_ior_with_set(self, raw_redis):
        raw_redis.flushdb()
        obj = _make_set_obj(raw_redis, "s1", {1, 2})
        obj |= {3}
        assert set(obj) == {1, 2, 3}

    def test_iand_with_set(self, raw_redis):
        raw_redis.flushdb()
        obj = _make_set_obj(raw_redis, "s1", {1, 2, 3})
        obj &= {2, 3, 4}
        assert set(obj) == {2, 3}

    def test_isub_with_set(self, raw_redis):
        raw_redis.flushdb()
        obj = _make_set_obj(raw_redis, "s1", {1, 2, 3})
        obj -= {2}
        assert set(obj) == {1, 3}

    def test_ixor_with_set(self, raw_redis):
        raw_redis.flushdb()
        obj = _make_set_obj(raw_redis, "s1", {1, 2, 3})
        obj ^= {2, 3, 4}
        assert set(obj) == {1, 4}


class TestSetDiverseElementTypes:
    def test_float_element_add_contains(self, ns):
        ns["s"] = set()
        s = ns.get_object("s")
        s.add(3.14)
        assert 3.14 in s

    def test_float_element_iter(self, ns):
        ns["s"] = set()
        s = ns.get_object("s")
        s.add(3.14)
        assert 3.14 in set(s)

    def test_float_element_discard(self, ns):
        ns["s"] = set()
        s = ns.get_object("s")
        s.add(3.14)
        s.discard(3.14)
        assert 3.14 not in s

    def test_bool_element_add_contains(self, ns):
        ns["s"] = set()
        s = ns.get_object("s")
        s.add(True)
        s.add(False)
        assert True in s
        assert False in s

    def test_none_element_add_contains(self, ns):
        ns["s"] = set()
        s = ns.get_object("s")
        s.add(None)
        assert None in s

    def test_none_element_discard(self, ns):
        ns["s"] = set()
        s = ns.get_object("s")
        s.add(None)
        s.discard(None)
        assert None not in s

    def test_datetime_element_add_contains(self, ns):
        ns["s"] = set()
        s = ns.get_object("s")
        dt = datetime.datetime(2026, 6, 1, 12, 0, tzinfo=datetime.UTC)
        s.add(dt)
        assert dt in s

    def test_datetime_element_iter_roundtrip(self, ns):
        ns["s"] = set()
        s = ns.get_object("s")
        dt = datetime.datetime(2026, 6, 1, 12, 0, tzinfo=datetime.UTC)
        s.add(dt)
        elements = set(s)
        assert dt in elements

    def test_mixed_types_together(self, ns):
        ns["s"] = set()
        s = ns.get_object("s")
        s.add(3.14)
        s.add(True)
        s.add(None)
        dt = datetime.datetime(2026, 6, 1, 12, 0, tzinfo=datetime.UTC)
        s.add(dt)
        assert len(s) == 4
        assert 3.14 in s
        assert True in s
        assert None in s
        assert dt in s


class TestTupleObject:
    def test_assign_and_read_tuple(self, ns):
        ns["t"] = (1, "x", 3.14)
        assert ns["t"] == (1, "x", 3.14)

    def test_get_object_returns_tuple_object(self, ns):
        ns["t"] = (1, "x", 3.14)
        obj = ns.get_object("t")
        assert isinstance(obj, TupleObject)
        assert obj.type == "scalar:tuple"
        assert obj.value == (1, "x", 3.14)

    def test_get_object_name_and_uri(self, ns):
        ns["mytuple"] = (1, 2)
        obj = ns.get_object("mytuple")
        assert obj.name == "mytuple"
        assert obj.uri == "gpdc://testdomain/testns/mytuple"

    def test_empty_tuple_as_value(self, ns):
        ns["empty"] = ()
        assert ns["empty"] == ()

    def test_empty_tuple_get_object(self, ns):
        ns["empty"] = ()
        obj = ns.get_object("empty")
        assert isinstance(obj, TupleObject)
        assert obj.value == ()

    def test_empty_tuple_in_list(self, ns):
        ns["lst"] = [1, (), 3]
        lst = ns.get_object("lst")
        assert lst.value == [1, (), 3]

    def test_empty_tuple_as_dict_value(self, ns):
        ns["d"] = {"a": ()}
        d = ns.get_object("d")
        assert d["a"] == ()

    def test_nonempty_tuple_as_dict_key(self, ns):
        ns["d"] = {(1, 2): "val"}
        d = ns.get_object("d")
        assert d[(1, 2)] == "val"

    def test_value_setter_updates_tuple(self, ns):
        ns["t"] = (1, 2, 3)
        obj = ns.get_object("t")
        obj.value = (4, 5, 6)
        assert obj.value == (4, 5, 6)
        assert ns["t"] == (4, 5, 6)

    def test_value_setter_rejects_wrong_type(self, ns):
        ns["t"] = (1, 2)
        obj = ns.get_object("t")
        with pytest.raises(TypeError, match="Cannot assign"):
            obj.value = [1, 2]

    def test_empty_tuple_as_dict_key_rejected(self, ns):
        ns["d"] = {"a": 1}
        d = ns.get_object("d")
        with pytest.raises(TypeError, match="empty tuple"):
            d[()] = "value"

    def test_empty_tuple_as_set_element_rejected(self, ns):
        ns["s"] = {1, 2}
        s = ns.get_object("s")
        with pytest.raises(TypeError, match="empty tuple"):
            s.add(())


class TestAnonymousObjectCreation:
    def test_anonymous_list_created_correctly(self, ns):
        ns["parent"] = {"items": [1, 2, 3]}
        d = ns.get_object("parent")
        result = d["items"]
        assert isinstance(result.value, list)
        assert result.value == [1, 2, 3]

    def test_anonymous_dict_created_correctly(self, ns):
        ns["parent"] = {"nested": {"a": 1}}
        d = ns.get_object("parent")
        nested = d["nested"]
        assert isinstance(nested.value, dict)
        assert nested.value == {"a": 1}

    def test_anonymous_set_created_correctly(self, ns):
        ns["parent"] = {"items": {1, 2, 3}}
        d = ns.get_object("parent")
        items = d["items"]
        assert isinstance(items.value, set)
        assert items.value == {1, 2, 3}

    def test_anonymous_nested_list_in_list(self, ns):
        ns["parent"] = [[1, 2], [3, 4]]
        lst = ns.get_object("parent")
        assert isinstance(lst.value, list)
        assert len(lst.value) == 2


class TestSetAlgebraReturnsPython:
    def test_union_returns_python_set(self, ns):
        ns["a"] = {1, 2}
        ns["b"] = {2, 3}
        a = ns.get_object("a")
        b = ns.get_object("b")
        result = a | b
        assert isinstance(result, set)
        assert not hasattr(result, "raw_key")
        assert result == {1, 2, 3}

    def test_intersection_returns_python_set(self, ns):
        ns["a"] = {1, 2, 3}
        ns["b"] = {2, 3, 4}
        a = ns.get_object("a")
        b = ns.get_object("b")
        result = a & b
        assert isinstance(result, set)
        assert result == {2, 3}

    def test_difference_returns_python_set(self, ns):
        ns["a"] = {1, 2, 3}
        ns["b"] = {2, 3, 4}
        a = ns.get_object("a")
        b = ns.get_object("b")
        result = a - b
        assert isinstance(result, set)
        assert result == {1}

    def test_symmetric_difference_returns_python_set(self, ns):
        ns["a"] = {1, 2}
        ns["b"] = {2, 3}
        a = ns.get_object("a")
        b = ns.get_object("b")
        result = a ^ b
        assert isinstance(result, set)
        assert result == {1, 3}

    def test_union_with_plain_set(self, ns):
        ns["a"] = {1, 2}
        a = ns.get_object("a")
        result = a | {2, 3}
        assert isinstance(result, set)
        assert result == {1, 2, 3}

    def test_inplace_union_still_returns_self(self, ns):
        ns["a"] = {1, 2}
        a = ns.get_object("a")
        a |= {2, 3}
        assert a.value == {1, 2, 3}


class TestSetScalarRestriction:
    def test_set_accepts_int(self):
        from gpdatacached.types import encode_key, decode_key

        encoded = encode_key(42)
        assert decode_key(encoded) == 42

    def test_set_accepts_str(self):
        from gpdatacached.types import encode_key, decode_key

        encoded = encode_key("hello")
        assert decode_key(encoded) == "hello"

    def test_set_accepts_float(self):
        from gpdatacached.types import encode_key, decode_key

        encoded = encode_key(3.14)
        assert decode_key(encoded) == 3.14

    def test_set_accepts_bool(self):
        from gpdatacached.types import encode_key, decode_key

        encoded = encode_key(True)
        assert decode_key(encoded) is True

    def test_set_accepts_tuple(self):
        from gpdatacached.types import encode_key, decode_key

        encoded = encode_key((1, 2))
        assert decode_key(encoded) == (1, 2)

    def test_set_accepts_datetime(self):
        from gpdatacached.types import encode_key, decode_key

        now = datetime.datetime(2024, 1, 1, 12, 0, 0)
        encoded = encode_key(now)
        decoded = decode_key(encoded)
        assert decoded == now

    def test_set_accepts_none(self):
        from gpdatacached.types import encode_key, decode_key

        encoded = encode_key(None)
        assert decode_key(encoded) is None

    def test_set_rejects_list(self):
        from gpdatacached.types import encode_key

        with pytest.raises(TypeError, match="container type"):
            encode_key([1, 2, 3])

    def test_set_rejects_dict(self):
        from gpdatacached.types import encode_key

        with pytest.raises(TypeError, match="container type"):
            encode_key({"a": 1})

    def test_set_rejects_set(self):
        from gpdatacached.types import encode_key

        with pytest.raises(TypeError, match="container type"):
            encode_key({1, 2})

    def test_setobject_add_rejects_list(self, ns):
        ns["s"] = {1, 2, 3}
        s = ns.get_object("s")
        with pytest.raises(TypeError, match="container type"):
            s.add([1, 2])

    def test_setobject_create_rejects_nested(self, ns):
        with pytest.raises(TypeError):
            ns["s"] = {1, [2, 3]}

    def test_setobject_copy_returns_python_set(self, ns):
        ns["s"] = {1, 2, 3}
        s = ns.get_object("s")
        result = s.copy()
        assert isinstance(result, set)
        assert result == {1, 2, 3}

    def test_setobject_deepcopy_same_as_copy(self, ns):
        ns["s"] = {1, 2, 3}
        s = ns.get_object("s")
        assert s.deepcopy() == s.copy()


class TestContainerErrorPaths:
    def test_list_index_out_of_range(self, ns):
        ns["lst"] = [1, 2, 3]
        lst = ns.get_object("lst")
        with pytest.raises(IndexError):
            lst[10]

    def test_list_pop_empty(self, ns):
        ns["lst"] = []
        lst = ns.get_object("lst")
        with pytest.raises(IndexError, match="empty list"):
            lst.pop()

    def test_list_remove_missing(self, ns):
        ns["lst"] = [1, 2, 3]
        lst = ns.get_object("lst")
        with pytest.raises(ValueError, match="not in list"):
            lst.remove(99)

    def test_dict_key_error(self, ns):
        ns["d"] = {"a": 1}
        d = ns.get_object("d")
        with pytest.raises(KeyError):
            d["nonexistent"]

    def test_dict_del_missing_key(self, ns):
        ns["d"] = {"a": 1}
        d = ns.get_object("d")
        with pytest.raises(KeyError):
            del d["nonexistent"]

    def test_set_pop_empty(self, ns):
        ns["s"] = set()
        s = ns.get_object("s")
        with pytest.raises(KeyError, match="empty set"):
            s.pop()

    def test_set_remove_missing(self, ns):
        ns["s"] = {1, 2}
        s = ns.get_object("s")
        with pytest.raises(KeyError):
            s.remove(99)
