"""Tests for namespace.py list/container support (Task 4)."""

from __future__ import annotations

import pytest
import redis as redis_lib

from gpdatacached.config import GPDCDomainConfig
from gpdatacached.domain import GPDCDomain
from gpdatacached.keys import make_object_key, make_raw_key, make_ref_key
from gpdatacached.objects import ListObject

DOMAIN = "testdomain_list"
NS = "testns_list"
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
    # Cleanup all keys used by tests
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


# ── Basic list creation ────────────────────────────────────────────────────────


class TestListCreation:
    def test_setitem_list_creates_obj_key(self, ns, r):
        ns["mylist"] = [1, 2, 3]
        obj_key = make_object_key(DOMAIN, NS, "mylist")
        assert r.exists(obj_key)
        assert r.hget(obj_key, "type") == "collection:list"

    def test_setitem_list_creates_raw_key(self, ns, r):
        ns["mylist"] = [1, 2, 3]
        raw_key = make_raw_key(DOMAIN, NS, "mylist")
        assert r.exists(raw_key)
        assert r.llen(raw_key) == 3

    def test_getitem_list_returns_list_object(self, ns):
        ns["mylist"] = [1, 2, 3]
        result = ns["mylist"]
        assert isinstance(result, ListObject)

    def test_getitem_list_index(self, ns):
        ns["mylist"] = [10, 20, 30]
        lst = ns["mylist"]
        assert lst[0] == 10
        assert lst[1] == 20
        assert lst[2] == 30

    def test_list_append(self, ns):
        ns["mylist"] = [1, 2, 3]
        lst = ns["mylist"]
        lst.append(4)
        assert list(ns["mylist"]) == [1, 2, 3, 4]

    def test_new_list_has_empty_referrers(self, ns):
        """Spec §9.3 step 5: ref: set must be empty immediately after creation."""
        ns["mylist"] = [1, 2, 3]
        obj = ns["mylist"]
        assert obj.referrers() == set()


# ── Reference sharing ──────────────────────────────────────────────────────────


class TestReferenceSharing:
    def test_assign_list_object_shares_raw_key(self, ns, r):
        ns["a"] = [1, 2, 3]
        ns["b"] = ns["a"]
        obj_a_key = make_object_key(DOMAIN, NS, "a")
        obj_b_key = make_object_key(DOMAIN, NS, "b")
        assert r.type(obj_b_key) == "string"
        assert r.get(obj_b_key) == f"ref:{obj_a_key}"

    def test_assign_list_object_adds_to_ref_set(self, ns, r):
        ns["a"] = [1, 2, 3]
        ns["b"] = ns["a"]
        ref_key = make_ref_key(DOMAIN, NS, "a")
        assert "b" in r.smembers(ref_key)

    def test_referrers_method(self, ns):
        ns["a"] = [1, 2, 3]
        ns["b"] = ns["a"]
        assert "b" in ns["a"].referrers()

    def test_shared_modification_visible_through_both(self, ns):
        ns["a"] = [1, 2, 3]
        ns["b"] = ns["a"]
        ns["b"].append(4)
        assert list(ns["a"]) == [1, 2, 3, 4]

    def test_no_raw_key_for_reference(self, ns, r):
        ns["a"] = [1, 2, 3]
        ns["b"] = ns["a"]
        raw_b = make_raw_key(DOMAIN, NS, "b")
        obj_b_key = make_object_key(DOMAIN, NS, "b")
        assert r.type(obj_b_key) == "string"
        assert r.get(obj_b_key).startswith("ref:")
        assert not r.exists(raw_b)


# ── copy() semantics ───────────────────────────────────────────────────────────


class TestCopySemantics:
    def test_copy_creates_independent_list(self, ns):
        ns["a"] = [1, 2, 3]
        ns["c"] = ns["a"].copy()
        ns["c"].append(4)
        assert list(ns["a"]) == [1, 2, 3]
        assert list(ns["c"]) == [1, 2, 3, 4]

    def test_copy_does_not_add_to_ref_set(self, ns, r):
        ns["a"] = [1, 2, 3]
        ns["c"] = ns["a"].copy()
        ref_key = make_ref_key(DOMAIN, NS, "a")
        assert "c" not in r.smembers(ref_key)

    def test_copy_creates_separate_raw_key(self, ns, r):
        ns["a"] = [1, 2, 3]
        ns["c"] = ns["a"].copy()
        raw_a = make_raw_key(DOMAIN, NS, "a")
        raw_c = make_raw_key(DOMAIN, NS, "c")
        obj_c_value = r.hget(make_object_key(DOMAIN, NS, "c"), "value")
        assert obj_c_value == raw_c
        assert obj_c_value != raw_a


# ── Re-assignment breaking reference ───────────────────────────────────────────


class TestReassignment:
    def test_reassign_removes_from_old_ref_set(self, ns, r):
        ns["x"] = [1, 2, 3]
        ns["y"] = ns["x"]
        ref_x = make_ref_key(DOMAIN, NS, "x")
        assert "y" in r.smembers(ref_x)
        ns["y"] = [4, 5, 6]
        assert "y" not in r.smembers(ref_x)

    def test_reassign_creates_new_raw_key(self, ns, r):
        ns["x"] = [1, 2, 3]
        ns["y"] = ns["x"]
        ns["y"] = [4, 5, 6]
        raw_y = make_raw_key(DOMAIN, NS, "y")
        assert r.exists(raw_y)
        assert r.llen(raw_y) == 3

    def test_reassign_new_list_independent(self, ns):
        ns["x"] = [1, 2, 3]
        ns["y"] = ns["x"]
        ns["y"] = [4, 5, 6]
        assert list(ns["x"]) == [1, 2, 3]
        assert list(ns["y"]) == [4, 5, 6]


# ── Deletion ──────────────────────────────────────────────────────────────────


class TestDeletion:
    def test_delete_no_referrers_removes_all_keys(self, ns, r):
        ns["a"] = [1, 2, 3]
        del ns["a"]
        assert not r.exists(make_object_key(DOMAIN, NS, "a"))
        assert not r.exists(make_raw_key(DOMAIN, NS, "a"))
        assert not r.exists(make_ref_key(DOMAIN, NS, "a"))

    @pytest.mark.skip(reason="Pending refactor")
    def test_delete_with_referrers_keeps_referrer_accessible(self, ns, r):
        ns["a"] = [1, 2, 3]
        ns["b"] = ns["a"]
        del ns["a"]
        assert not r.exists(make_object_key(DOMAIN, NS, "a"))
        assert not r.exists(make_raw_key(DOMAIN, NS, "a"))
        assert list(ns["b"]) == [1, 2, 3]

    def test_delete_referrer_removes_from_original_ref_set(self, ns, r):
        ns["a"] = [1, 2, 3]
        ns["b"] = ns["a"]
        del ns["b"]
        assert not r.exists(make_object_key(DOMAIN, NS, "b"))
        ref_a = make_ref_key(DOMAIN, NS, "a")
        assert "b" not in r.smembers(ref_a)

    def test_delete_referrer_leaves_raw_data_intact(self, ns, r):
        """Deleting a referencing object must NOT delete the shared raw data."""
        ns["a"] = [1, 2, 3]
        ns["b"] = ns["a"]

        raw_key_a = r.hget(make_object_key(DOMAIN, NS, "a"), "value")

        del ns["b"]

        # raw:a must still exist
        assert r.exists(raw_key_a) == 1
        assert r.lrange(raw_key_a, 0, -1) == ["int:1", "int:2", "int:3"]

        # a is still accessible
        lst = ns["a"]
        assert list(lst) == [1, 2, 3]

    def test_delete_nonexistent_raises_key_error(self, ns):
        with pytest.raises(KeyError):
            del ns["nonexistent"]


# ── Mixed scalar and list ──────────────────────────────────────────────────────


class TestMixedTypes:
    def test_scalar_still_works(self, ns):
        ns["x"] = 42
        assert ns["x"] == 42

    def test_scalar_and_list_coexist(self, ns):
        ns["x"] = 42
        ns["mylist"] = [1, 2, 3]
        assert ns["x"] == 42
        assert isinstance(ns["mylist"], ListObject)
        assert list(ns["mylist"]) == [1, 2, 3]
