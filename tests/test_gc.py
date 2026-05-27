"""Tests for GPDC garbage collection (Phase 4, Section 6)."""

from __future__ import annotations

import pytest
import redis as redis_lib

from gpdatacached.config import GPDCDomainConfig
from gpdatacached.domain import GPDCDomain
from gpdatacached.keys import make_object_key, make_raw_key, make_ref_key

REDIS_HOST = "127.0.0.1"
REDIS_PORT = 6379
REDIS_DB = 1
REDIS_PASSWORD = "123456"

DOMAIN = "testdomain_gc"
NS = "testns_gc"


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


class TestGCOrphanRaw:
    def test_gc_removes_orphan_raw_key(self, ns, r):
        ns["x"] = [1, 2, 3]
        raw_x = make_raw_key(DOMAIN, NS, "x")
        r.delete(make_object_key(DOMAIN, NS, "x"))
        r.delete(make_ref_key(DOMAIN, NS, "x"))

        assert r.exists(raw_x)
        ns.domain.run_gc(namespace=NS)
        assert not r.exists(raw_x)


class TestGCOrphanRef:
    def test_gc_removes_orphan_ref_key(self, ns, r):
        ns["x"] = [1, 2, 3]
        ref_x = make_ref_key(DOMAIN, NS, "x")
        r.sadd(ref_x, "orphan_referrer")
        r.delete(make_object_key(DOMAIN, NS, "x"))
        r.delete(make_raw_key(DOMAIN, NS, "x"))

        assert r.exists(ref_x)
        ns.domain.run_gc(namespace=NS)
        assert not r.exists(ref_x)


class TestGCOrphanObj:
    def test_gc_removes_orphan_obj_with_missing_raw(self, ns, r):
        ns["x"] = [1, 2, 3]
        r.delete(make_raw_key(DOMAIN, NS, "x"))

        obj_x = make_object_key(DOMAIN, NS, "x")
        assert r.exists(obj_x)
        ns.domain.run_gc(namespace=NS)
        assert not r.exists(obj_x)


class TestGCPreservesValidData:
    def test_gc_preserves_named_objects(self, ns, r):
        ns["x"] = [1, 2, 3]
        ns.domain.run_gc(namespace=NS)
        assert r.exists(make_object_key(DOMAIN, NS, "x"))
        assert r.exists(make_raw_key(DOMAIN, NS, "x"))

    def test_gc_preserves_referenced_raw(self, ns, r):
        ns["x"] = [1, 2, 3]
        ns["y"] = ns["x"]

        raw_x = make_raw_key(DOMAIN, NS, "x")
        ns.domain.run_gc(namespace=NS)
        assert r.exists(raw_x)

    def test_gc_preserves_scalar_objects(self, ns, r):
        ns["s"] = 42
        ns.domain.run_gc(namespace=NS)
        assert r.exists(make_object_key(DOMAIN, NS, "s"))


class TestGCMultipleOrphans:
    def test_gc_cleans_multiple_orphans(self, ns, r):
        ns["a"] = [1]
        ns["b"] = [2]
        ns["c"] = [3]

        r.delete(make_object_key(DOMAIN, NS, "a"))
        r.delete(make_ref_key(DOMAIN, NS, "a"))
        r.delete(make_object_key(DOMAIN, NS, "b"))
        r.delete(make_ref_key(DOMAIN, NS, "b"))

        ns.domain.run_gc(namespace=NS)
        assert not r.exists(make_raw_key(DOMAIN, NS, "a"))
        assert not r.exists(make_raw_key(DOMAIN, NS, "b"))
        assert r.exists(make_raw_key(DOMAIN, NS, "c"))


class TestGCDisabled:
    def test_gc_disabled_does_not_start_thread(self, r):
        config = GPDCDomainConfig(
            redis_host=REDIS_HOST,
            redis_port=REDIS_PORT,
            redis_db=REDIS_DB,
            redis_password=REDIS_PASSWORD,
            gc_enabled=False,
        )
        domain = GPDCDomain("gc_disabled_test", config)
        assert not hasattr(domain, "_gc_thread") or domain._gc_thread is None


class TestGCCleanupOnDomainClose:
    def test_domain_close_stops_gc(self, r):
        config = GPDCDomainConfig(
            redis_host=REDIS_HOST,
            redis_port=REDIS_PORT,
            redis_db=REDIS_DB,
            redis_password=REDIS_PASSWORD,
        )
        domain = GPDCDomain("gc_close_test", config)
        domain.close()
        assert domain._gc_thread is None or not domain._gc_thread.is_alive()


class TestGCWithStringAliases:
    """GC handles the new alias-as-string model."""

    def test_gc_cleans_dangling_alias(self, ns, r):
        """Alias whose canonical owner has been removed by TTL expiry gets cleaned."""
        from gpdatacached.gc import run_gc_for_namespace

        ns["owner"] = [1, 2, 3]
        ns["alias"] = ns["owner"]
        owner_key = make_object_key(DOMAIN, NS, "owner")
        raw_owner = make_raw_key(DOMAIN, NS, "owner")
        ref_owner = make_ref_key(DOMAIN, NS, "owner")
        r.delete(owner_key, raw_owner, ref_owner)

        deleted = run_gc_for_namespace(r, DOMAIN, NS)
        assert deleted > 0
        alias_key = make_object_key(DOMAIN, NS, "alias")
        assert not r.exists(alias_key)

    def test_gc_preserves_valid_alias(self, ns, r):
        """GC does not touch aliases that still point to valid owners."""
        from gpdatacached.gc import run_gc_for_namespace

        ns["owner"] = [1, 2, 3]
        ns["alias"] = ns["owner"]
        run_gc_for_namespace(r, DOMAIN, NS)
        assert "alias" in ns
        assert "owner" in ns

    def test_gc_cleans_stale_ref_set_member(self, ns, r):
        """GC removes ref set members whose obj keys no longer exist."""
        from gpdatacached.gc import run_gc_for_namespace

        ns["owner"] = [1, 2, 3]
        ns["alias"] = ns["owner"]
        alias_key = make_object_key(DOMAIN, NS, "alias")
        r.delete(alias_key)

        run_gc_for_namespace(r, DOMAIN, NS)
        ref_key = make_ref_key(DOMAIN, NS, "owner")
        assert "alias" not in r.smembers(ref_key)


class TestGCStaleRefSetMembers:
    """GC removes stale ref-set members whose bindings no longer match."""

    def test_gc_removes_alias_reassigned_to_different_owner(self, ns, r):
        """Alias reassigned to a different owner is removed from old owner's ref set."""
        from gpdatacached.gc import run_gc_for_namespace

        ns["owner_a"] = [1, 2, 3]
        ns["owner_b"] = [10, 20]
        ns["alias"] = ns["owner_a"]

        alias_key = make_object_key(DOMAIN, NS, "alias")
        owner_b_key = make_object_key(DOMAIN, NS, "owner_b")
        r.set(alias_key, f"ref:{owner_b_key}")
        ref_key_a = make_ref_key(DOMAIN, NS, "owner_a")
        assert "alias" in r.smembers(ref_key_a)

        run_gc_for_namespace(r, DOMAIN, NS)

        assert "alias" not in r.smembers(ref_key_a)

    def test_gc_removes_container_member_no_longer_referencing(self, ns, r):
        """Container holder removed from ref set when its raw data no longer refs the canonical owner."""
        from gpdatacached.gc import run_gc_for_namespace

        ns["target"] = [100, 200]
        ns["holder"] = [1, 2]
        holder_raw_key = make_raw_key(DOMAIN, NS, "holder")

        ns["holder"].append(ns["target"])

        ref_key = make_ref_key(DOMAIN, NS, "target")
        assert "holder" in r.smembers(ref_key)

        r.delete(holder_raw_key)
        r.rpush(holder_raw_key, "plain_value")

        run_gc_for_namespace(r, DOMAIN, NS)
        assert "holder" not in r.smembers(ref_key)

    def test_gc_preserves_container_member_still_referencing(self, ns, r):
        """Container holder kept in ref set when its raw data still refs the canonical owner."""
        from gpdatacached.gc import run_gc_for_namespace

        ns["target"] = [100, 200]
        ns["holder"] = [1, 2]
        ns["holder"].append(ns["target"])

        ref_key = make_ref_key(DOMAIN, NS, "target")
        assert "holder" in r.smembers(ref_key)

        run_gc_for_namespace(r, DOMAIN, NS)
        assert "holder" in r.smembers(ref_key)

    def test_gc_removes_non_collection_hash_member_from_ref_set(self, ns, r):
        """Scalar hash member (non-collection) in ref set is removed."""
        from gpdatacached.gc import run_gc_for_namespace

        ns["target"] = [100, 200]
        ns["scalar"] = 42
        ref_key = make_ref_key(DOMAIN, NS, "target")
        r.sadd(ref_key, "scalar")
        assert "scalar" in r.smembers(ref_key)

        run_gc_for_namespace(r, DOMAIN, NS)
        assert "scalar" not in r.smembers(ref_key)
