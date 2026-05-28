from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from unittest.mock import MagicMock, call

from gpdatacached.ttl import TTLPolicy
from gpdatacached.meta import ObjectMeta, apply_lifecycle, effective_policy


class TestObjectMetaFromRedis:
    def test_parse_full_container_meta(self):
        data = {
            "type": "collection:list",
            "codec": "native",
            "value": "raw:some:key",
            "cache_mode": "ttl",
            "ttl": "300",
            "created_at": "2025-01-01T00:00:00+00:00",
            "updated_at": "2025-01-02T00:00:00+00:00",
            "item_count": "5",
        }
        meta = ObjectMeta.from_redis(data)
        assert meta.type == "collection:list"
        assert meta.codec == "native"
        assert meta.value == "raw:some:key"
        assert meta.cache_mode == "ttl"
        assert meta.ttl == 300
        assert meta.created_at == datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        assert meta.updated_at == datetime(2025, 1, 2, 0, 0, 0, tzinfo=timezone.utc)
        assert meta.item_count == 5

    def test_parse_minimal_scalar_meta(self):
        data = {
            "type": "scalar:int",
            "value": "scalar:int:42",
        }
        meta = ObjectMeta.from_redis(data)
        assert meta.type == "scalar:int"
        assert meta.value == "scalar:int:42"
        assert meta.codec == "native"
        assert meta.cache_mode == "permanent"
        assert meta.ttl == 0
        assert meta.item_count is None

    def test_parse_empty_meta_defaults_to_permanent(self):
        data = {"type": "scalar:str", "cache_mode": "", "ttl": ""}
        meta = ObjectMeta.from_redis(data)
        assert meta.cache_mode == "permanent"
        assert meta.ttl == 0

    def test_invalid_cache_mode_raises(self):
        with pytest.raises(ValidationError, match="cache_mode"):
            ObjectMeta(type="scalar:int", cache_mode="invalid")

    def test_negative_ttl_raises(self):
        with pytest.raises(ValidationError, match="ttl must be >= 0"):
            ObjectMeta(type="scalar:int", ttl=-1)


class TestObjectMetaToRedisMapping:
    def test_roundtrip_container(self):
        now = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        original = ObjectMeta(
            type="collection:dict",
            codec="native",
            value="raw:abc",
            cache_mode="sliding",
            ttl=60,
            created_at=now,
            updated_at=now,
            item_count=3,
        )
        mapping = original.to_redis_mapping()
        assert mapping == {
            "type": "collection:dict",
            "codec": "native",
            "value": "raw:abc",
            "cache_mode": "sliding",
            "ttl": "60",
            "created_at": "2025-06-01T12:00:00+00:00",
            "updated_at": "2025-06-01T12:00:00+00:00",
            "item_count": "3",
        }
        restored = ObjectMeta.from_redis(mapping)
        assert restored.type == original.type
        assert restored.cache_mode == original.cache_mode
        assert restored.ttl == original.ttl
        assert restored.item_count == original.item_count

    def test_roundtrip_scalar(self):
        original = ObjectMeta(
            type="scalar:int",
            value="scalar:int:42",
        )
        mapping = original.to_redis_mapping()
        assert "item_count" not in mapping
        assert mapping["ttl"] == ""
        assert "created_at" not in mapping
        restored = ObjectMeta.from_redis(mapping)
        assert restored.type == original.type
        assert restored.ttl == 0
        assert restored.item_count is None


class TestObjectMetaHelpers:
    def test_is_collection_true(self):
        meta = ObjectMeta(type="collection:list")
        assert meta.is_collection() is True

    def test_is_collection_false(self):
        meta = ObjectMeta(type="scalar:int")
        assert meta.is_collection() is False

    def test_is_inherited_true(self):
        meta = ObjectMeta(type="collection:list", cache_mode="inherited")
        assert meta.is_inherited() is True

    def test_is_inherited_false(self):
        meta = ObjectMeta(type="collection:list", cache_mode="ttl")
        assert meta.is_inherited() is False

    def test_declares_expiry_true(self):
        meta = ObjectMeta(type="scalar:int", cache_mode="ttl", ttl=300)
        assert meta.declares_expiry() is True

    def test_declares_expiry_false_permanent(self):
        meta = ObjectMeta(type="scalar:int", cache_mode="permanent", ttl=300)
        assert meta.declares_expiry() is False

    def test_declares_expiry_false_inherited(self):
        meta = ObjectMeta(type="collection:list", cache_mode="inherited", ttl=60)
        assert meta.declares_expiry() is False


class TestApplyLifecycle:
    def test_applies_ttl_to_obj_key(self):
        r = MagicMock()
        policy = TTLPolicy(cache_mode="ttl", declared_ttl=300, expire_seconds=300)
        apply_lifecycle(r, "gpdc:d:ns:obj:x", "gpdc:d:ns:raw:x", "gpdc:d:ns:ref:x", True, policy)
        r.expire.assert_any_call("gpdc:d:ns:obj:x", 300)

    def test_applies_ttl_to_raw_and_ref_for_container(self):
        r = MagicMock()
        policy = TTLPolicy(cache_mode="ttl", declared_ttl=300, expire_seconds=300)
        apply_lifecycle(r, "gpdc:d:ns:obj:x", "gpdc:d:ns:raw:x", "gpdc:d:ns:ref:x", True, policy)
        assert call("gpdc:d:ns:obj:x", 300) in r.expire.call_args_list
        assert call("gpdc:d:ns:raw:x", 300) in r.expire.call_args_list
        assert call("gpdc:d:ns:ref:x", 300) in r.expire.call_args_list

    def test_persist_all_keys(self):
        r = MagicMock()
        policy = TTLPolicy(cache_mode="permanent", declared_ttl=0, expire_seconds=None)
        apply_lifecycle(r, "gpdc:d:ns:obj:x", "gpdc:d:ns:raw:x", "gpdc:d:ns:ref:x", True, policy)
        r.persist.assert_any_call("gpdc:d:ns:obj:x")
        r.persist.assert_any_call("gpdc:d:ns:raw:x")
        r.persist.assert_any_call("gpdc:d:ns:ref:x")

    def test_scalar_skips_raw_and_ref(self):
        r = MagicMock()
        policy = TTLPolicy(cache_mode="ttl", declared_ttl=300, expire_seconds=300)
        apply_lifecycle(r, "gpdc:d:ns:obj:x", "", "", False, policy)
        r.expire.assert_called_once_with("gpdc:d:ns:obj:x", 300)


class TestEffectivePolicy:
    def test_non_inherited_returns_own_policy(self):
        meta = ObjectMeta.from_redis({"type": "collection:list", "cache_mode": "ttl", "ttl": "300"})
        r = MagicMock()
        policy = effective_policy(meta, r, "d", "ns", "somename")
        assert policy.cache_mode == "ttl"
        assert policy.expire_seconds == 300

    def test_permanent_non_inherited(self):
        meta = ObjectMeta.from_redis({"type": "scalar:int", "cache_mode": "permanent"})
        r = MagicMock()
        policy = effective_policy(meta, r, "d", "ns", "somename")
        assert policy.cache_mode == "permanent"
        assert policy.expire_seconds is None

    def test_inherited_with_permanent_parent(self):
        meta = ObjectMeta.from_redis({"type": "collection:list", "cache_mode": "inherited", "ttl": "0"})
        r = MagicMock()
        r.smembers.return_value = {"parent1"}
        r.hgetall.return_value = {"type": "collection:dict", "cache_mode": "permanent", "ttl": ""}
        policy = effective_policy(meta, r, "d", "ns", "~anon:child1")
        assert policy.cache_mode == "permanent"
        assert policy.expire_seconds is None

    def test_inherited_with_ttl_parent(self):
        meta = ObjectMeta.from_redis({"type": "collection:list", "cache_mode": "inherited", "ttl": "0"})
        r = MagicMock()
        r.smembers.return_value = {"parent1"}

        def hgetall_side_effect(key):
            return {"type": "collection:dict", "cache_mode": "ttl", "ttl": "300"}

        r.hgetall.side_effect = hgetall_side_effect
        r.ttl.return_value = 250
        policy = effective_policy(meta, r, "d", "ns", "~anon:child1")
        assert policy.cache_mode == "ttl"
        assert policy.expire_seconds == 250

    def test_inherited_with_no_parents(self):
        meta = ObjectMeta.from_redis({"type": "collection:list", "cache_mode": "inherited", "ttl": "0"})
        r = MagicMock()
        r.smembers.return_value = set()
        policy = effective_policy(meta, r, "d", "ns", "~anon:child1")
        assert policy.cache_mode == "permanent"
        assert policy.expire_seconds is None
