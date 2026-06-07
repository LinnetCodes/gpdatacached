from __future__ import annotations

from unittest.mock import patch, MagicMock

from gpdatacached.config import GPDCDomainConfig
from gpdatacached.domain import GPDCDomain
from gpdatacached.gc import run_gc_for_namespace
from gpdatacached.keys import (
    make_object_key,
    make_raw_key,
    make_ref_key,
    make_raw_key_prefix,
)

DOMAIN = "testdomain"
NS = "testns"


class TestGCOrphanRaw:
    def test_gc_removes_orphan_raw_key(self, ns, clean_redis):
        ns["x"] = [1, 2, 3]
        raw_x = make_raw_key(DOMAIN, NS, "x")
        clean_redis.delete(make_object_key(DOMAIN, NS, "x"))
        clean_redis.delete(make_ref_key(DOMAIN, NS, "x"))

        assert clean_redis.exists(raw_x)
        ns.domain.run_gc(namespace=NS)
        assert not clean_redis.exists(raw_x)


class TestGCOrphanRef:
    def test_gc_removes_orphan_ref_key(self, ns, clean_redis):
        ns["x"] = [1, 2, 3]
        ref_x = make_ref_key(DOMAIN, NS, "x")
        clean_redis.sadd(ref_x, "orphan_referrer")
        clean_redis.delete(make_object_key(DOMAIN, NS, "x"))
        clean_redis.delete(make_raw_key(DOMAIN, NS, "x"))

        assert clean_redis.exists(ref_x)
        ns.domain.run_gc(namespace=NS)
        assert not clean_redis.exists(ref_x)


class TestGCOrphanObj:
    def test_gc_preserves_corrupted_container_with_missing_raw(self, ns, clean_redis):
        ns["x"] = [1, 2, 3]
        clean_redis.delete(make_raw_key(DOMAIN, NS, "x"))

        obj_x = make_object_key(DOMAIN, NS, "x")
        assert clean_redis.exists(obj_x)
        ns.domain.run_gc(namespace=NS)
        assert clean_redis.exists(obj_x), "corrupted container should be preserved, not auto-deleted"


class TestGCPreservesValidData:
    def test_gc_preserves_named_objects(self, ns, clean_redis):
        ns["x"] = [1, 2, 3]
        ns.domain.run_gc(namespace=NS)
        assert clean_redis.exists(make_object_key(DOMAIN, NS, "x"))
        assert clean_redis.exists(make_raw_key(DOMAIN, NS, "x"))

    def test_gc_preserves_referenced_raw(self, ns, clean_redis):
        ns["x"] = [1, 2, 3]
        ns["y"] = ns["x"]

        raw_x = make_raw_key(DOMAIN, NS, "x")
        ns.domain.run_gc(namespace=NS)
        assert clean_redis.exists(raw_x)

    def test_gc_preserves_scalar_objects(self, ns, clean_redis):
        ns["s"] = 42
        ns.domain.run_gc(namespace=NS)
        assert clean_redis.exists(make_object_key(DOMAIN, NS, "s"))


class TestGCMultipleOrphans:
    def test_gc_cleans_multiple_orphans(self, ns, clean_redis):
        ns["a"] = [1]
        ns["b"] = [2]
        ns["c"] = [3]

        clean_redis.delete(make_object_key(DOMAIN, NS, "a"))
        clean_redis.delete(make_ref_key(DOMAIN, NS, "a"))
        clean_redis.delete(make_object_key(DOMAIN, NS, "b"))
        clean_redis.delete(make_ref_key(DOMAIN, NS, "b"))

        ns.domain.run_gc(namespace=NS)
        assert not clean_redis.exists(make_raw_key(DOMAIN, NS, "a"))
        assert not clean_redis.exists(make_raw_key(DOMAIN, NS, "b"))
        assert clean_redis.exists(make_raw_key(DOMAIN, NS, "c"))


class TestGCDisabled:
    def test_gc_disabled_does_not_start_thread(self, domain):
        assert not hasattr(domain, "_gc_thread") or domain._gc_thread is None


class TestGCCleanupOnDomainClose:
    def test_domain_close_stops_gc(self, clean_redis):
        config = GPDCDomainConfig(
            redis_host="127.0.0.1",
            redis_port=6379,
            redis_db=1,
            redis_password="123456",
            gc_enabled=True,
        )
        domain = GPDCDomain("testdomain", config)
        domain.close()
        assert domain._gc_thread is None or not domain._gc_thread.is_alive()


class TestGCWithStringAliases:
    def test_gc_cleans_dangling_alias(self, ns, clean_redis):
        ns["owner"] = [1, 2, 3]
        ns["alias"] = ns["owner"]
        owner_key = make_object_key(DOMAIN, NS, "owner")
        raw_owner = make_raw_key(DOMAIN, NS, "owner")
        ref_owner = make_ref_key(DOMAIN, NS, "owner")
        clean_redis.delete(owner_key, raw_owner, ref_owner)

        deleted = run_gc_for_namespace(clean_redis, DOMAIN, NS)
        assert deleted > 0
        alias_key = make_object_key(DOMAIN, NS, "alias")
        assert not clean_redis.exists(alias_key)

    def test_gc_preserves_valid_alias(self, ns, clean_redis):
        ns["owner"] = [1, 2, 3]
        ns["alias"] = ns["owner"]
        run_gc_for_namespace(clean_redis, DOMAIN, NS)
        assert "alias" in ns
        assert "owner" in ns

    def test_gc_cleans_stale_ref_set_member(self, ns, clean_redis):
        ns["owner"] = [1, 2, 3]
        ns["alias"] = ns["owner"]
        alias_key = make_object_key(DOMAIN, NS, "alias")
        clean_redis.delete(alias_key)

        run_gc_for_namespace(clean_redis, DOMAIN, NS)
        ref_key = make_ref_key(DOMAIN, NS, "owner")
        assert "alias" not in clean_redis.smembers(ref_key)


class TestGCStaleRefSetMembers:
    def test_gc_removes_alias_reassigned_to_different_owner(self, ns, clean_redis):
        ns["owner_a"] = [1, 2, 3]
        ns["owner_b"] = [10, 20]
        ns["alias"] = ns["owner_a"]

        alias_key = make_object_key(DOMAIN, NS, "alias")
        owner_b_key = make_object_key(DOMAIN, NS, "owner_b")
        clean_redis.set(alias_key, f"ref:{owner_b_key}")
        ref_key_a = make_ref_key(DOMAIN, NS, "owner_a")
        assert "alias" in clean_redis.smembers(ref_key_a)

        run_gc_for_namespace(clean_redis, DOMAIN, NS)

        assert "alias" not in clean_redis.smembers(ref_key_a)

    def test_gc_removes_container_member_no_longer_referencing(self, ns, clean_redis):
        ns["target"] = [100, 200]
        ns["holder"] = [1, 2]
        holder_raw_key = make_raw_key(DOMAIN, NS, "holder")

        ns["holder"].append(ns["target"])

        ref_key = make_ref_key(DOMAIN, NS, "target")
        assert "holder" in clean_redis.smembers(ref_key)

        clean_redis.delete(holder_raw_key)
        clean_redis.rpush(holder_raw_key, "plain_value")

        run_gc_for_namespace(clean_redis, DOMAIN, NS)
        assert "holder" not in clean_redis.smembers(ref_key)

    def test_gc_preserves_container_member_still_referencing(self, ns, clean_redis):
        ns["target"] = [100, 200]
        ns["holder"] = [1, 2]
        ns["holder"].append(ns["target"])

        ref_key = make_ref_key(DOMAIN, NS, "target")
        assert "holder" in clean_redis.smembers(ref_key)

        run_gc_for_namespace(clean_redis, DOMAIN, NS)
        assert "holder" in clean_redis.smembers(ref_key)

    def test_gc_removes_non_collection_hash_member_from_ref_set(self, ns, clean_redis):
        ns["target"] = [100, 200]
        ns["scalar"] = 42
        ref_key = make_ref_key(DOMAIN, NS, "target")
        clean_redis.sadd(ref_key, "scalar")
        assert "scalar" in clean_redis.smembers(ref_key)

        run_gc_for_namespace(clean_redis, DOMAIN, NS)
        assert "scalar" not in clean_redis.smembers(ref_key)


class TestGCSingleRoundCleanup:
    def test_orphan_raw_with_stale_ref_cleaned_in_one_round(self, ns, clean_redis):
        ns["target"] = [100, 200]
        ns["holder"] = [1, 2]
        ns["holder"].append(ns["target"])

        holder_raw = make_raw_key(DOMAIN, NS, "holder")
        clean_redis.delete(holder_raw)
        clean_redis.rpush(holder_raw, "val1", "val2")

        clean_redis.delete(make_object_key(DOMAIN, NS, "target"))

        deleted = run_gc_for_namespace(clean_redis, DOMAIN, NS)
        assert deleted > 0

        raw_prefix = make_raw_key_prefix(DOMAIN, NS)
        remaining = [k for k in clean_redis.scan_iter(match=f"{raw_prefix}*") if "target" in k]
        assert remaining == [], f"Orphan target raw key should be deleted in one GC round, found: {remaining}"


class TestGCNoNamespaces:
    def test_run_gc_empty_domain(self, domain):
        deleted = domain.run_gc()
        assert deleted == 0


class TestGCLogging:
    def test_gc_loop_logs_exceptions(self, domain, caplog):
        domain.ensure_namespace("testns")
        stop_event = MagicMock()
        stop_event.wait.side_effect = [False, True]
        domain._gc_stop_event = stop_event
        with patch("gpdatacached.gc.run_gc_for_namespace", side_effect=RuntimeError("test error")):
            domain._gc_loop()
        assert any("GPDC GC error" in record.message for record in caplog.records)


class TestGCEmptyContainers:
    def test_gc_preserves_empty_list(self, ns, clean_redis):
        ns["empty_lst"] = []
        run_gc_for_namespace(clean_redis, DOMAIN, NS)
        obj_key = make_object_key(DOMAIN, NS, "empty_lst")
        assert clean_redis.exists(obj_key), "empty list obj key should survive GC"
        assert "empty_lst" in ns

    def test_gc_preserves_empty_dict(self, ns, clean_redis):
        ns["empty_dct"] = {}
        run_gc_for_namespace(clean_redis, DOMAIN, NS)
        obj_key = make_object_key(DOMAIN, NS, "empty_dct")
        assert clean_redis.exists(obj_key), "empty dict obj key should survive GC"
        assert "empty_dct" in ns

    def test_gc_preserves_empty_set(self, ns, clean_redis):
        ns["empty_st"] = set()
        run_gc_for_namespace(clean_redis, DOMAIN, NS)
        obj_key = make_object_key(DOMAIN, NS, "empty_st")
        assert clean_redis.exists(obj_key), "empty set obj key should survive GC"
        assert "empty_st" in ns


class TestGCClearedContainers:
    def test_gc_preserves_cleared_list(self, ns, clean_redis):
        ns["lst"] = [1, 2, 3]
        lst = ns["lst"]
        lst.clear()
        run_gc_for_namespace(clean_redis, DOMAIN, NS)
        obj_key = make_object_key(DOMAIN, NS, "lst")
        assert clean_redis.exists(obj_key), "cleared list obj key should survive GC"
        assert len(ns["lst"]) == 0

    def test_gc_preserves_cleared_dict(self, ns, clean_redis):
        ns["dct"] = {"a": 1, "b": 2}
        dct = ns["dct"]
        dct.clear()
        run_gc_for_namespace(clean_redis, DOMAIN, NS)
        obj_key = make_object_key(DOMAIN, NS, "dct")
        assert clean_redis.exists(obj_key), "cleared dict obj key should survive GC"
        assert len(ns["dct"]) == 0

    def test_gc_preserves_cleared_set(self, ns, clean_redis):
        ns["st"] = {1, 2, 3}
        st = ns["st"]
        st.clear()
        run_gc_for_namespace(clean_redis, DOMAIN, NS)
        obj_key = make_object_key(DOMAIN, NS, "st")
        assert clean_redis.exists(obj_key), "cleared set obj key should survive GC"
        assert len(ns["st"]) == 0


class TestGCCorruptedContainer:
    def test_gc_preserves_corrupted_list(self, ns, clean_redis):
        ns["corrupted_lst"] = [1, 2, 3]
        clean_redis.delete(make_raw_key(DOMAIN, NS, "corrupted_lst"))
        obj_key = make_object_key(DOMAIN, NS, "corrupted_lst")
        run_gc_for_namespace(clean_redis, DOMAIN, NS)
        assert clean_redis.exists(obj_key), "corrupted container should be preserved for diagnosis"

    def test_gc_preserves_corrupted_dict(self, ns, clean_redis):
        ns["corrupted_dct"] = {"a": 1}
        clean_redis.delete(make_raw_key(DOMAIN, NS, "corrupted_dct"))
        obj_key = make_object_key(DOMAIN, NS, "corrupted_dct")
        run_gc_for_namespace(clean_redis, DOMAIN, NS)
        assert clean_redis.exists(obj_key), "corrupted container should be preserved for diagnosis"

    def test_gc_preserves_corrupted_set(self, ns, clean_redis):
        ns["corrupted_st"] = {1, 2}
        clean_redis.delete(make_raw_key(DOMAIN, NS, "corrupted_st"))
        obj_key = make_object_key(DOMAIN, NS, "corrupted_st")
        run_gc_for_namespace(clean_redis, DOMAIN, NS)
        assert clean_redis.exists(obj_key), "corrupted container should be preserved for diagnosis"


class TestGCItemCountMaintenance:
    def test_item_count_after_empty_list_creation(self, ns, clean_redis):
        ns["lst"] = []
        obj_key = make_object_key(DOMAIN, NS, "lst")
        assert clean_redis.hget(obj_key, "item_count") == "0"

    def test_item_count_after_nonempty_list_creation(self, ns, clean_redis):
        ns["lst"] = [1, 2, 3]
        obj_key = make_object_key(DOMAIN, NS, "lst")
        assert clean_redis.hget(obj_key, "item_count") == "3"

    def test_item_count_after_list_append(self, ns, clean_redis):
        ns["lst"] = [1]
        ns["lst"].append(2)
        obj_key = make_object_key(DOMAIN, NS, "lst")
        assert clean_redis.hget(obj_key, "item_count") == "2"

    def test_item_count_after_list_clear(self, ns, clean_redis):
        ns["lst"] = [1, 2]
        ns["lst"].clear()
        obj_key = make_object_key(DOMAIN, NS, "lst")
        assert clean_redis.hget(obj_key, "item_count") == "0"

    def test_item_count_after_dict_setitem(self, ns, clean_redis):
        ns["dct"] = {}
        ns["dct"]["key"] = "val"
        obj_key = make_object_key(DOMAIN, NS, "dct")
        assert clean_redis.hget(obj_key, "item_count") == "1"

    def test_item_count_after_dict_clear(self, ns, clean_redis):
        ns["dct"] = {"a": 1}
        ns["dct"].clear()
        obj_key = make_object_key(DOMAIN, NS, "dct")
        assert clean_redis.hget(obj_key, "item_count") == "0"

    def test_item_count_after_set_add(self, ns, clean_redis):
        ns["st"] = set()
        ns["st"].add(1)
        obj_key = make_object_key(DOMAIN, NS, "st")
        assert clean_redis.hget(obj_key, "item_count") == "1"

    def test_item_count_after_set_clear(self, ns, clean_redis):
        ns["st"] = {1, 2}
        ns["st"].clear()
        obj_key = make_object_key(DOMAIN, NS, "st")
        assert clean_redis.hget(obj_key, "item_count") == "0"
