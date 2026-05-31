# tests/test_objects_container.py
"""TDD tests for GPDCContainerObject and ListObject."""

from __future__ import annotations

import datetime

import pytest
import redis as redis_lib

from gpdatacached.codec import ListCodec
from gpdatacached.keys import make_object_key, make_raw_key, make_ref_key
from gpdatacached import ListObject

REDIS_HOST = "127.0.0.1"
REDIS_PORT = 6379
REDIS_DB = 1
REDIS_PASSWORD = "123456"

DOMAIN = "testdomain"
NS = "testns"
OBJ_NAME = "mylist"


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
def setup_list(r):
    """Flush DB and set up a minimal ListObject backed by Redis."""
    r.flushdb()

    obj_key = make_object_key(DOMAIN, NS, OBJ_NAME)
    raw_key = make_raw_key(DOMAIN, NS, OBJ_NAME)
    ref_key = make_ref_key(DOMAIN, NS, OBJ_NAME)

    now = datetime.datetime.now(datetime.UTC).isoformat()
    r.hset(
        obj_key,
        mapping={
            "name": OBJ_NAME,
            "type": "collection:list",
            "cache_mode": "permanent",
            "ttl": "0",
            "created_at": now,
            "updated_at": now,
        },
    )

    # Build a minimal namespace/domain stub
    class FakeDomain:
        name = DOMAIN
        redis = r

    class FakeNamespace:
        name = NS
        domain = FakeDomain()

        def _redis(self):
            return r

    ns = FakeNamespace()

    # seed: [10, 20, 30]
    ListCodec.create_raw(ns, raw_key, [10, 20, 30], OBJ_NAME, "permanent", 0)

    obj = ListObject(OBJ_NAME, ns)
    return obj, r, obj_key, raw_key, ref_key, ns


# ── len ───────────────────────────────────────────────────────────────────────


def test_len(setup_list):
    obj, *_ = setup_list
    assert len(obj) == 3


# ── __getitem__ ───────────────────────────────────────────────────────────────


def test_getitem_positive(setup_list):
    obj, *_ = setup_list
    assert obj[0] == 10
    assert obj[1] == 20
    assert obj[2] == 30


def test_getitem_negative(setup_list):
    obj, *_ = setup_list
    assert obj[-1] == 30
    assert obj[-2] == 20


# ── __setitem__ ───────────────────────────────────────────────────────────────


def test_setitem(setup_list):
    obj, *_ = setup_list
    obj[1] = 99
    assert obj[1] == 99
    assert len(obj) == 3


# ── __delitem__ ───────────────────────────────────────────────────────────────


def test_delitem(setup_list):
    obj, *_ = setup_list
    del obj[1]
    assert len(obj) == 2
    assert list(obj) == [10, 30]


def test_delitem_first(setup_list):
    obj, *_ = setup_list
    del obj[0]
    assert list(obj) == [20, 30]


def test_delitem_last(setup_list):
    obj, *_ = setup_list
    del obj[-1]
    assert list(obj) == [10, 20]


# ── append ────────────────────────────────────────────────────────────────────


def test_append(setup_list):
    obj, *_ = setup_list
    obj.append(40)
    assert len(obj) == 4
    assert obj[-1] == 40


# ── extend ────────────────────────────────────────────────────────────────────


def test_extend(setup_list):
    obj, *_ = setup_list
    obj.extend([40, 50])
    assert len(obj) == 5
    assert list(obj)[-2:] == [40, 50]


# ── insert ────────────────────────────────────────────────────────────────────


def test_insert_at_start(setup_list):
    obj, *_ = setup_list
    obj.insert(0, 5)
    assert obj[0] == 5
    assert list(obj) == [5, 10, 20, 30]


def test_insert_in_middle(setup_list):
    obj, *_ = setup_list
    obj.insert(2, 15)
    assert list(obj) == [10, 20, 15, 30]


# ── pop ───────────────────────────────────────────────────────────────────────


def test_pop_last(setup_list):
    obj, *_ = setup_list
    val = obj.pop()
    assert val == 30
    assert len(obj) == 2


def test_pop_first(setup_list):
    obj, *_ = setup_list
    val = obj.pop(0)
    assert val == 10
    assert list(obj) == [20, 30]


# ── remove ────────────────────────────────────────────────────────────────────


def test_remove(setup_list):
    obj, *_ = setup_list
    obj.append(10)  # [10, 20, 30, 10]
    obj.remove(10)
    assert list(obj) == [20, 30, 10]


# ── reverse ───────────────────────────────────────────────────────────────────


def test_reverse(setup_list):
    obj, *_ = setup_list
    obj.reverse()
    assert list(obj) == [30, 20, 10]


# ── sort ──────────────────────────────────────────────────────────────────────


def test_sort(setup_list):
    obj, r, obj_key, raw_key, ref_key, ns = setup_list
    # Reseed unsorted
    ListCodec.delete_raw(ns, raw_key)
    ListCodec.create_raw(ns, raw_key, [30, 10, 20], OBJ_NAME, "permanent", 0)
    obj.sort()
    assert list(obj) == [10, 20, 30]


# ── count / index / contains ──────────────────────────────────────────────────


def test_count(setup_list):
    obj, *_ = setup_list
    obj.append(10)
    assert obj.count(10) == 2
    assert obj.count(99) == 0


def test_index(setup_list):
    obj, *_ = setup_list
    assert obj.index(20) == 1


def test_contains(setup_list):
    obj, *_ = setup_list
    assert 20 in obj
    assert 99 not in obj


# ── iteration ─────────────────────────────────────────────────────────────────


def test_iteration(setup_list):
    obj, *_ = setup_list
    assert list(obj) == [10, 20, 30]


# ── clear ─────────────────────────────────────────────────────────────────────


def test_clear(setup_list):
    obj, *_ = setup_list
    obj.clear()
    assert len(obj) == 0
    assert list(obj) == []


# ── value property ────────────────────────────────────────────────────────────


def test_value_getter(setup_list):
    obj, *_ = setup_list
    assert obj.value == [10, 20, 30]


def test_value_setter_rejected(setup_list):
    obj, *_ = setup_list

    with pytest.raises(
        TypeError,
        match="Container value replacement is not supported; use explicit mutating methods instead",
    ):
        obj.value = [1, 2, 3]

    assert obj.value == [10, 20, 30]


# ── referrers ─────────────────────────────────────────────────────────────────


def test_referrers_empty(setup_list):
    obj, *_ = setup_list
    assert obj.referrers() == set()


def test_referrers_with_data(setup_list):
    obj, r, obj_key, raw_key, ref_key, ns = setup_list
    r.sadd(ref_key, "other_obj")
    result = obj.referrers()
    assert result == {"other_obj"}


# ── copy / deepcopy ───────────────────────────────────────────────────────────


def test_copy_returns_list(setup_list):
    obj, *_ = setup_list
    result = obj.copy()
    assert result == [10, 20, 30]
    assert isinstance(result, list)


def test_deepcopy_returns_list(setup_list):
    obj, *_ = setup_list
    result = obj.deepcopy()
    assert result == [10, 20, 30]
    assert isinstance(result, list)


# ── updated_at refreshed on write ops ────────────────────────────────────────


def test_write_updates_updated_at(setup_list):
    import time

    obj, r, obj_key, *_ = setup_list
    before = r.hget(obj_key, "updated_at")
    time.sleep(0.01)
    obj.append(99)
    after = r.hget(obj_key, "updated_at")
    assert after >= before  # updated_at should be refreshed


# ── type ──────────────────────────────────────────────────────────────────────


def test_type(setup_list):
    obj, *_ = setup_list
    assert obj.type == "collection:list"
