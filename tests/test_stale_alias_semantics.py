"""Tests for P0-3: stale alias semantics in public mapping interface.

When a canonical owner expires or is deleted, the alias binding (Redis string)
remains until GC runs. These tests verify that all public mapping methods
treat stale aliases as non-existent, regardless of GC state.
"""

import pytest
from gpdatacached.keys import make_object_key, make_ref_key


class TestStaleAliasContains:
    """__contains__ returns False for stale aliases."""

    def test_stale_alias_not_in_ns(self, ns):
        ns["owner"] = [1, 2, 3]
        ns["alias"] = ns["owner"]
        r = ns._redis()
        owner_key = make_object_key(ns.domain.name, ns.name, "owner")
        ref_key = make_ref_key(ns.domain.name, ns.name, "owner")
        r.delete(owner_key)
        r.delete(ref_key)
        assert "alias" not in ns

    def test_valid_alias_in_ns(self, ns):
        ns["owner"] = [1, 2, 3]
        ns["alias"] = ns["owner"]
        assert "alias" in ns

    def test_owner_still_in_ns_after_owner_deleted(self, ns):
        ns["owner"] = [1, 2, 3]
        assert "owner" in ns
        del ns["owner"]
        assert "owner" not in ns

    def test_stale_alias_string_key_still_exists_in_redis(self, ns):
        ns["owner"] = [1, 2, 3]
        ns["alias"] = ns["owner"]
        r = ns._redis()
        owner_key = make_object_key(ns.domain.name, ns.name, "owner")
        ref_key = make_ref_key(ns.domain.name, ns.name, "owner")
        r.delete(owner_key)
        r.delete(ref_key)
        alias_key = make_object_key(ns.domain.name, ns.name, "alias")
        assert r.exists(alias_key) > 0
        assert "alias" not in ns


class TestStaleAliasGetItem:
    """__getitem__ raises KeyError for stale aliases."""

    def test_stale_alias_getitem_raises_keyerror(self, ns):
        ns["owner"] = [1, 2, 3]
        ns["alias"] = ns["owner"]
        r = ns._redis()
        owner_key = make_object_key(ns.domain.name, ns.name, "owner")
        ref_key = make_ref_key(ns.domain.name, ns.name, "owner")
        r.delete(owner_key)
        r.delete(ref_key)
        with pytest.raises(KeyError):
            ns["alias"]

    def test_valid_alias_getitem_works(self, ns):
        ns["owner"] = [1, 2, 3]
        ns["alias"] = ns["owner"]
        assert ns["alias"].value == [1, 2, 3]


class TestStaleAliasIter:
    """__iter__ does not yield stale alias names."""

    def test_iter_excludes_stale_alias(self, ns):
        ns["owner"] = [1, 2, 3]
        ns["alias"] = ns["owner"]
        ns["other"] = "hello"
        r = ns._redis()
        owner_key = make_object_key(ns.domain.name, ns.name, "owner")
        ref_key = make_ref_key(ns.domain.name, ns.name, "owner")
        r.delete(owner_key)
        r.delete(ref_key)
        names = list(ns)
        assert "alias" not in names
        assert "other" in names

    def test_iter_includes_valid_alias(self, ns):
        ns["owner"] = [1, 2, 3]
        ns["alias"] = ns["owner"]
        names = list(ns)
        assert "alias" in names
        assert "owner" in names

    def test_iter_multiple_stale_and_valid(self, ns):
        ns["owner1"] = [1, 2]
        ns["owner2"] = [3, 4]
        ns["alias1"] = ns["owner1"]
        ns["alias2"] = ns["owner2"]
        ns["standalone"] = "val"
        r = ns._redis()
        owner1_key = make_object_key(ns.domain.name, ns.name, "owner1")
        ref1_key = make_ref_key(ns.domain.name, ns.name, "owner1")
        r.delete(owner1_key)
        r.delete(ref1_key)
        names = list(ns)
        assert "alias1" not in names
        assert "owner1" not in names
        assert "alias2" in names
        assert "owner2" in names
        assert "standalone" in names


class TestStaleAliasLen:
    """__len__ does not count stale aliases."""

    def test_len_excludes_stale_alias(self, ns):
        ns["owner"] = [1, 2, 3]
        ns["alias"] = ns["owner"]
        ns["other"] = "hello"
        r = ns._redis()
        owner_key = make_object_key(ns.domain.name, ns.name, "owner")
        ref_key = make_ref_key(ns.domain.name, ns.name, "owner")
        r.delete(owner_key)
        r.delete(ref_key)
        assert len(ns) == 1
        assert set(ns) == {"other"}

    def test_stale_alias_not_counted_fix(self, ns):
        ns["owner"] = [1, 2, 3]
        ns["alias"] = ns["owner"]
        r = ns._redis()
        owner_key = make_object_key(ns.domain.name, ns.name, "owner")
        ref_key = make_ref_key(ns.domain.name, ns.name, "owner")
        r.delete(owner_key)
        r.delete(ref_key)
        assert len(ns) == 0

    def test_len_counts_valid_alias(self, ns):
        ns["owner"] = [1, 2, 3]
        ns["alias"] = ns["owner"]
        assert len(ns) == 2
