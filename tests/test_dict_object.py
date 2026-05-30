"""TDD tests for DictObject (MutableMapping backed by Redis Hash)."""

from __future__ import annotations

import datetime
import time

import pytest
import redis as redis_lib

from gpdatacached.codec import DictCodec
from gpdatacached.keys import make_object_key, make_raw_key, make_ref_key
from gpdatacached.objects import DictObject

REDIS_HOST = "127.0.0.1"
REDIS_PORT = 6379
REDIS_DB = 1
REDIS_PASSWORD = "123456"

DOMAIN = "testdomain"
NS = "testns"
OBJ_NAME = "mydict"


@pytest.fixture(scope="session")
def r():
    return redis_lib.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD,
        decode_responses=True,
    )


@pytest.fixture
def setup_dict(r):
    r.flushdb()

    obj_key = make_object_key(DOMAIN, NS, OBJ_NAME)
    raw_key = make_raw_key(DOMAIN, NS, OBJ_NAME)
    ref_key = make_ref_key(DOMAIN, NS, OBJ_NAME)

    now = datetime.datetime.now(datetime.UTC).isoformat()
    r.hset(
        obj_key,
        mapping={
            "type": "collection:dict",
            "codec": "native",
            "value": raw_key,
            "cache_mode": "permanent",
            "ttl": "",
            "created_at": now,
            "updated_at": now,
        },
    )

    class FakeDomain:
        name = DOMAIN
        redis = r

    class FakeNamespace:
        name = NS
        domain = FakeDomain()

        def _redis(self):
            return r

        def _cascade_delete_anonymous(self, anon_name):
            obj_key = make_object_key(DOMAIN, NS, anon_name)
            meta = r.hgetall(obj_key)
            if not meta:
                return

            raw_key = meta.get("value", "")
            ref_key = make_ref_key(DOMAIN, NS, anon_name)

            if raw_key:
                r.delete(raw_key)
            r.delete(ref_key)
            r.delete(obj_key)

    ns = FakeNamespace()

    DictCodec.delete_raw(ns, raw_key)
    DictCodec.create_raw(ns, raw_key, {"name": "Alice", "age": 30}, OBJ_NAME, "permanent", 0)

    obj = DictObject(OBJ_NAME, ns)
    return obj, r, obj_key, raw_key, ref_key


def test_type(setup_dict):
    obj, *_ = setup_dict
    assert obj.type == "collection:dict"


def test_len(setup_dict):
    obj, *_ = setup_dict
    assert len(obj) == 2


def test_getitem_str_key(setup_dict):
    obj, *_ = setup_dict
    assert obj["name"] == "Alice"


def test_getitem_int_key(setup_dict):
    obj, *_ = setup_dict
    assert obj["age"] == 30


def test_getitem_missing_raises_key_error(setup_dict):
    obj, *_ = setup_dict
    with pytest.raises(KeyError):
        obj["nonexistent"]


def test_setitem_overwrite(setup_dict):
    obj, *_ = setup_dict
    obj["name"] = "Bob"
    assert obj["name"] == "Bob"


def test_setitem_new_key(setup_dict):
    obj, *_ = setup_dict
    obj["city"] = "NYC"
    assert obj["city"] == "NYC"
    assert len(obj) == 3


def test_setitem_existing_container_slot_rejects_scalar(setup_dict):
    obj, *_ = setup_dict
    obj["nested"] = [1, 2, 3]

    with pytest.raises(TypeError, match="Cannot overwrite an existing container slot"):
        obj["nested"] = 1


def test_setitem_existing_container_slot_rejects_container(setup_dict):
    obj, *_ = setup_dict
    obj["nested"] = [1, 2, 3]

    with pytest.raises(TypeError, match="Cannot overwrite an existing container slot"):
        obj["nested"] = {"x": 1}


def test_delete_then_recreate_container_slot_is_allowed(setup_dict):
    obj, *_ = setup_dict
    obj["nested"] = [1, 2, 3]
    del obj["nested"]

    obj["nested"] = {"x": 1}

    assert obj["nested"]["x"] == 1


def test_delitem(setup_dict):
    obj, *_ = setup_dict
    del obj["age"]
    assert len(obj) == 1
    assert "age" not in obj


def test_delitem_missing_raises_key_error(setup_dict):
    obj, *_ = setup_dict
    with pytest.raises(KeyError):
        del obj["nonexistent"]


def test_contains_true(setup_dict):
    obj, *_ = setup_dict
    assert "name" in obj


def test_contains_false(setup_dict):
    obj, *_ = setup_dict
    assert "nonexistent" not in obj


def test_iter(setup_dict):
    obj, *_ = setup_dict
    keys = set(obj)
    assert keys == {"name", "age"}


def test_keys(setup_dict):
    obj, *_ = setup_dict
    assert set(obj.keys()) == {"name", "age"}


def test_values(setup_dict):
    obj, *_ = setup_dict
    assert set(obj.values()) == {"Alice", 30}


def test_items(setup_dict):
    obj, *_ = setup_dict
    result = dict(obj.items())
    assert result == {"name": "Alice", "age": 30}


def test_get_existing(setup_dict):
    obj, *_ = setup_dict
    assert obj.get("name") == "Alice"


def test_get_missing_default(setup_dict):
    obj, *_ = setup_dict
    assert obj.get("missing", "fallback") == "fallback"


def test_get_missing_no_default(setup_dict):
    obj, *_ = setup_dict
    assert obj.get("missing") is None


def test_pop_existing(setup_dict):
    obj, *_ = setup_dict
    val = obj.pop("age")
    assert val == 30
    assert "age" not in obj


def test_pop_missing_with_default(setup_dict):
    obj, *_ = setup_dict
    val = obj.pop("missing", 99)
    assert val == 99


def test_pop_missing_raises(setup_dict):
    obj, *_ = setup_dict
    with pytest.raises(KeyError):
        obj.pop("missing")


def test_setdefault_existing(setup_dict):
    obj, *_ = setup_dict
    val = obj.setdefault("name", "Default")
    assert val == "Alice"


def test_setdefault_new(setup_dict):
    obj, *_ = setup_dict
    val = obj.setdefault("country", "US")
    assert val == "US"
    assert obj["country"] == "US"


def test_update(setup_dict):
    obj, *_ = setup_dict
    obj.update({"age": 31, "city": "LA"})
    assert obj["age"] == 31
    assert obj["city"] == "LA"


def test_clear(setup_dict):
    obj, *_ = setup_dict
    obj.clear()
    assert len(obj) == 0
    assert dict(obj.items()) == {}


def test_value_getter(setup_dict):
    obj, *_ = setup_dict
    assert obj.value == {"name": "Alice", "age": 30}


def test_value_setter_rejected(setup_dict):
    obj, *_ = setup_dict

    with pytest.raises(
        TypeError,
        match="Container value replacement is not supported; use explicit mutating methods instead",
    ):
        obj.value = {"x": 1, "y": 2}

    assert obj.value == {"name": "Alice", "age": 30}


def test_tuple_key_setget(setup_dict):
    obj, *_ = setup_dict
    obj[("a", "b")] = "example"
    assert obj[("a", "b")] == "example"


def test_tuple_key_contains(setup_dict):
    obj, *_ = setup_dict
    obj[("a", "b")] = "example"
    assert ("a", "b") in obj


def test_tuple_key_delete(setup_dict):
    obj, *_ = setup_dict
    obj[("a", "b")] = "example"
    del obj[("a", "b")]
    assert ("a", "b") not in obj


def test_write_updates_updated_at(setup_dict):
    obj, r, obj_key, *_ = setup_dict
    before = r.hget(obj_key, "updated_at")
    time.sleep(0.01)
    obj["newkey"] = "newval"
    after = r.hget(obj_key, "updated_at")
    assert after >= before


def test_update_calls_touch_once(setup_dict):
    from unittest.mock import patch

    obj, *_ = setup_dict
    with patch.object(type(obj), "_touch", wraps=obj._touch) as mock_touch:
        obj.update({"a": 1, "b": 2, "c": 3})
        assert mock_touch.call_count == 1
    assert obj["a"] == 1
    assert obj["b"] == 2
    assert obj["c"] == 3


def test_update_empty_dict_no_touch(setup_dict):
    from unittest.mock import patch

    obj, *_ = setup_dict
    with patch.object(type(obj), "_touch", wraps=obj._touch) as mock_touch:
        obj.update({})
        assert mock_touch.call_count == 0


def test_update_kwargs_calls_touch_once(setup_dict):
    from unittest.mock import patch

    obj, *_ = setup_dict
    with patch.object(type(obj), "_touch", wraps=obj._touch) as mock_touch:
        obj.update(x=10, y=20)
        assert mock_touch.call_count == 1
    assert obj["x"] == 10
    assert obj["y"] == 20


def test_update_item_count(setup_dict):
    obj, r, obj_key, *_ = setup_dict
    assert len(obj) == 2
    obj.update({"c": 3, "d": 4, "e": 5})
    assert len(obj) == 5
    item_count = r.hget(obj_key, "item_count")
    assert item_count == "5"


def test_update_overwrites_existing(setup_dict):
    obj, *_ = setup_dict
    obj.update({"name": "Bob", "age": 99})
    assert obj["name"] == "Bob"
    assert obj["age"] == 99
