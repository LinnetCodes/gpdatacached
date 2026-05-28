"""Tests for P0-1: empty container GC fix."""

from __future__ import annotations

import pytest
import redis as redis_lib

from gpdatacached.config import GPDCDomainConfig
from gpdatacached.domain import GPDCDomain
from gpdatacached.gc import run_gc_for_namespace
from gpdatacached.keys import make_object_key, make_raw_key

REDIS_HOST = "127.0.0.1"
REDIS_PORT = 6379
REDIS_DB = 1
REDIS_PASSWORD = "123456"

DOMAIN = "testdomain_p01"
NS = "testns_p01"


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


class TestGCEmptyContainers:
    """Empty containers survive GC."""

    def test_gc_preserves_empty_list(self, ns, r):
        ns["empty_lst"] = []
        run_gc_for_namespace(r, DOMAIN, NS)
        obj_key = make_object_key(DOMAIN, NS, "empty_lst")
        assert r.exists(obj_key), "empty list obj key should survive GC"
        assert "empty_lst" in ns

    def test_gc_preserves_empty_dict(self, ns, r):
        ns["empty_dct"] = {}
        run_gc_for_namespace(r, DOMAIN, NS)
        obj_key = make_object_key(DOMAIN, NS, "empty_dct")
        assert r.exists(obj_key), "empty dict obj key should survive GC"
        assert "empty_dct" in ns

    def test_gc_preserves_empty_set(self, ns, r):
        ns["empty_st"] = set()
        run_gc_for_namespace(r, DOMAIN, NS)
        obj_key = make_object_key(DOMAIN, NS, "empty_st")
        assert r.exists(obj_key), "empty set obj key should survive GC"
        assert "empty_st" in ns


class TestGCClearedContainers:
    """Containers that have been cleared survive GC."""

    def test_gc_preserves_cleared_list(self, ns, r):
        ns["lst"] = [1, 2, 3]
        lst = ns["lst"]
        lst.clear()
        run_gc_for_namespace(r, DOMAIN, NS)
        obj_key = make_object_key(DOMAIN, NS, "lst")
        assert r.exists(obj_key), "cleared list obj key should survive GC"
        assert len(ns["lst"]) == 0

    def test_gc_preserves_cleared_dict(self, ns, r):
        ns["dct"] = {"a": 1, "b": 2}
        dct = ns["dct"]
        dct.clear()
        run_gc_for_namespace(r, DOMAIN, NS)
        obj_key = make_object_key(DOMAIN, NS, "dct")
        assert r.exists(obj_key), "cleared dict obj key should survive GC"
        assert len(ns["dct"]) == 0

    def test_gc_preserves_cleared_set(self, ns, r):
        ns["st"] = {1, 2, 3}
        st = ns["st"]
        st.clear()
        run_gc_for_namespace(r, DOMAIN, NS)
        obj_key = make_object_key(DOMAIN, NS, "st")
        assert r.exists(obj_key), "cleared set obj key should survive GC"
        assert len(ns["st"]) == 0


class TestGCCorruptedContainer:
    """Corrupted containers (item_count > 0 but no raw) are NOT auto-deleted by GC."""

    def test_gc_preserves_corrupted_list(self, ns, r):
        ns["corrupted_lst"] = [1, 2, 3]
        r.delete(make_raw_key(DOMAIN, NS, "corrupted_lst"))
        obj_key = make_object_key(DOMAIN, NS, "corrupted_lst")
        run_gc_for_namespace(r, DOMAIN, NS)
        assert r.exists(obj_key), "corrupted container should be preserved for diagnosis"

    def test_gc_preserves_corrupted_dict(self, ns, r):
        ns["corrupted_dct"] = {"a": 1}
        r.delete(make_raw_key(DOMAIN, NS, "corrupted_dct"))
        obj_key = make_object_key(DOMAIN, NS, "corrupted_dct")
        run_gc_for_namespace(r, DOMAIN, NS)
        assert r.exists(obj_key), "corrupted container should be preserved for diagnosis"

    def test_gc_preserves_corrupted_set(self, ns, r):
        ns["corrupted_st"] = {1, 2}
        r.delete(make_raw_key(DOMAIN, NS, "corrupted_st"))
        obj_key = make_object_key(DOMAIN, NS, "corrupted_st")
        run_gc_for_namespace(r, DOMAIN, NS)
        assert r.exists(obj_key), "corrupted container should be preserved for diagnosis"


class TestGCItemCountMaintenance:
    """item_count is correctly maintained across container operations."""

    def test_item_count_after_empty_list_creation(self, ns, r):
        ns["lst"] = []
        obj_key = make_object_key(DOMAIN, NS, "lst")
        assert r.hget(obj_key, "item_count") == "0"

    def test_item_count_after_nonempty_list_creation(self, ns, r):
        ns["lst"] = [1, 2, 3]
        obj_key = make_object_key(DOMAIN, NS, "lst")
        assert r.hget(obj_key, "item_count") == "3"

    def test_item_count_after_list_append(self, ns, r):
        ns["lst"] = [1]
        ns["lst"].append(2)
        obj_key = make_object_key(DOMAIN, NS, "lst")
        assert r.hget(obj_key, "item_count") == "2"

    def test_item_count_after_list_clear(self, ns, r):
        ns["lst"] = [1, 2]
        ns["lst"].clear()
        obj_key = make_object_key(DOMAIN, NS, "lst")
        assert r.hget(obj_key, "item_count") == "0"

    def test_item_count_after_dict_setitem(self, ns, r):
        ns["dct"] = {}
        ns["dct"]["key"] = "val"
        obj_key = make_object_key(DOMAIN, NS, "dct")
        assert r.hget(obj_key, "item_count") == "1"

    def test_item_count_after_dict_clear(self, ns, r):
        ns["dct"] = {"a": 1}
        ns["dct"].clear()
        obj_key = make_object_key(DOMAIN, NS, "dct")
        assert r.hget(obj_key, "item_count") == "0"

    def test_item_count_after_set_add(self, ns, r):
        ns["st"] = set()
        ns["st"].add(1)
        obj_key = make_object_key(DOMAIN, NS, "st")
        assert r.hget(obj_key, "item_count") == "1"

    def test_item_count_after_set_clear(self, ns, r):
        ns["st"] = {1, 2}
        ns["st"].clear()
        obj_key = make_object_key(DOMAIN, NS, "st")
        assert r.hget(obj_key, "item_count") == "0"
