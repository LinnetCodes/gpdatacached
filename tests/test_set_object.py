"""TDD tests for SetObject (MutableSet backed by Redis Set)."""

from __future__ import annotations

import datetime

import pytest
import redis as redis_lib

from gpdatacached.codec import SetCodec
from gpdatacached.keys import make_object_key, make_raw_key, make_ref_key
from gpdatacached.objects import SetObject

REDIS_HOST = "127.0.0.1"
REDIS_PORT = 6379
REDIS_DB = 1
REDIS_PASSWORD = "123456"

DOMAIN = "testdomain"
NS = "testns"
OBJ_NAME = "myset"


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
def setup_set(r):
    r.flushdb()

    obj_key = make_object_key(DOMAIN, NS, OBJ_NAME)
    raw_key = make_raw_key(DOMAIN, NS, OBJ_NAME)
    ref_key = make_ref_key(DOMAIN, NS, OBJ_NAME)

    now = datetime.datetime.now(datetime.UTC).isoformat()
    r.hset(
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
        redis = r

    class FakeNamespace:
        name = NS
        domain = FakeDomain()

        def _redis(self):
            return r

    ns = FakeNamespace()

    SetCodec.delete_raw(ns, raw_key)
    SetCodec.create_raw(ns, raw_key, {1, 2, 3}, OBJ_NAME, "permanent", 0)

    obj = SetObject(OBJ_NAME, ns)
    return obj, r, obj_key, raw_key, ref_key


class TestSetObjectBasic:
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


class TestSetObjectMutableSetOps:
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

    def test_pop_empty_set_raises_keyerror(self, r):
        r.flushdb()
        obj_key = make_object_key(DOMAIN, NS, "emptyset")
        raw_key = make_raw_key(DOMAIN, NS, "emptyset")
        now = datetime.datetime.now(datetime.UTC).isoformat()
        r.hset(
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
            redis = r

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


class TestSetObjectDeepCopy:
    def test_deepcopy_returns_python_set(self, setup_set):
        obj, *_ = setup_set
        result = obj.deepcopy()
        assert isinstance(result, set)
        assert result == {1, 2, 3}


class TestSetObjectBinaryOps:
    def test_union_with_set(self, r):
        r.flushdb()
        obj = _make_set_obj(r, "s1", {1, 2, 3})
        result = obj | {3, 4}
        assert isinstance(result, set)
        assert result == {1, 2, 3, 4}

    def test_intersection_with_set(self, r):
        r.flushdb()
        obj = _make_set_obj(r, "s1", {1, 2, 3})
        result = obj & {2, 3, 4}
        assert isinstance(result, set)
        assert result == {2, 3}

    def test_difference_with_set(self, r):
        r.flushdb()
        obj = _make_set_obj(r, "s1", {1, 2, 3})
        result = obj - {2}
        assert isinstance(result, set)
        assert result == {1, 3}

    def test_symmetric_difference_with_set(self, r):
        r.flushdb()
        obj = _make_set_obj(r, "s1", {1, 2, 3})
        result = obj ^ {2, 3, 4}
        assert isinstance(result, set)
        assert result == {1, 4}

    def test_union_with_setobject(self, r):
        r.flushdb()
        s1 = _make_set_obj(r, "s1", {1, 2})
        s2 = _make_set_obj(r, "s2", {2, 3})
        result = s1 | s2
        assert isinstance(result, set)
        assert result == {1, 2, 3}


class TestSetObjectInPlaceOps:
    def test_ior_with_set(self, r):
        r.flushdb()
        obj = _make_set_obj(r, "s1", {1, 2})
        obj |= {3}
        assert set(obj) == {1, 2, 3}

    def test_iand_with_set(self, r):
        r.flushdb()
        obj = _make_set_obj(r, "s1", {1, 2, 3})
        obj &= {2, 3, 4}
        assert set(obj) == {2, 3}

    def test_isub_with_set(self, r):
        r.flushdb()
        obj = _make_set_obj(r, "s1", {1, 2, 3})
        obj -= {2}
        assert set(obj) == {1, 3}

    def test_ixor_with_set(self, r):
        r.flushdb()
        obj = _make_set_obj(r, "s1", {1, 2, 3})
        obj ^= {2, 3, 4}
        assert set(obj) == {1, 4}


def _make_set_obj(r, name, value):
    from gpdatacached.keys import make_object_key as _mok, make_raw_key as _mrk
    import datetime as _dt

    obj_key = _mok(DOMAIN, NS, name)
    raw_key = _mrk(DOMAIN, NS, name)
    now = _dt.datetime.now(_dt.UTC).isoformat()
    r.hset(
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

    class _FD:
        name = DOMAIN
        redis = r

    class _FN:
        name = NS
        domain = _FD()

        def _redis(self):
            return r

    ns = _FN()
    SetCodec.create_raw(ns, raw_key, value, name, "permanent", 0)

    return SetObject(name, ns)
