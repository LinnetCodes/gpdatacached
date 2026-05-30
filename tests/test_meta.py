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
            "value": "raw:some:key",
            "cache_mode": "ttl",
            "ttl": "300",
            "created_at": "2025-01-01T00:00:00+00:00",
            "updated_at": "2025-01-02T00:00:00+00:00",
            "item_count": "5",
        }
        meta = ObjectMeta.from_redis(data)
        assert meta.type == "collection:list"
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


class TestObjectMetaLoad:
    def test_load_existing_key(self):
        r = MagicMock()
        r.hgetall.return_value = {
            "type": "scalar:int",
            "value": "int:42",
            "cache_mode": "permanent",
            "ttl": "",
        }
        meta = ObjectMeta.load(r, "gpdc:d:ns:obj:x")
        assert meta is not None
        assert meta.type == "scalar:int"
        assert meta.value == "int:42"
        assert meta.cache_mode == "permanent"
        assert meta.ttl == 0
        r.hgetall.assert_called_once_with("gpdc:d:ns:obj:x")

    def test_load_missing_key_returns_none(self):
        r = MagicMock()
        r.hgetall.return_value = {}
        meta = ObjectMeta.load(r, "gpdc:d:ns:obj:missing")
        assert meta is None

    def test_load_full_container(self):
        r = MagicMock()
        r.hgetall.return_value = {
            "type": "collection:list",
            "value": "gpdc:d:ns:raw:x",
            "cache_mode": "ttl",
            "ttl": "300",
            "created_at": "2025-01-01T00:00:00+00:00",
            "updated_at": "2025-01-02T00:00:00+00:00",
            "item_count": "5",
        }
        meta = ObjectMeta.load(r, "gpdc:d:ns:obj:x")
        assert meta is not None
        assert meta.type == "collection:list"
        assert meta.cache_mode == "ttl"
        assert meta.ttl == 300
        assert meta.item_count == 5
        assert meta.is_collection() is True


class TestObjectMetaSave:
    def test_save_writes_mapping_to_redis(self):
        r = MagicMock()
        meta = ObjectMeta(
            type="scalar:int",
            value="int:42",
            cache_mode="ttl",
            ttl=300,
        )
        meta.save(r, "gpdc:d:ns:obj:x")
        r.hset.assert_called_once()
        call_args = r.hset.call_args
        assert call_args[0][0] == "gpdc:d:ns:obj:x"
        mapping = call_args[1]["mapping"]
        assert mapping["type"] == "scalar:int"
        assert mapping["cache_mode"] == "ttl"
        assert mapping["ttl"] == "300"
        assert mapping["value"] == "int:42"

    def test_save_roundtrip(self):
        r = MagicMock()
        original = ObjectMeta(
            type="collection:dict",
            value="gpdc:d:ns:raw:mydict",
            cache_mode="sliding",
            ttl=60,
            created_at=datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
            updated_at=datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
            item_count=3,
        )
        original.save(r, "gpdc:d:ns:obj:mydict")
        call_args = r.hset.call_args
        mapping = call_args[1]["mapping"]
        restored = ObjectMeta.from_redis(mapping)
        assert restored.type == original.type
        assert restored.value == original.value
        assert restored.cache_mode == original.cache_mode
        assert restored.ttl == original.ttl
        assert restored.item_count == original.item_count


class TestObjectMetaWithUpdates:
    def test_with_updates_returns_new_instance(self):
        original = ObjectMeta(type="scalar:int", value="int:1", cache_mode="permanent", ttl=0)
        updated = original.with_updates(value="int:2", cache_mode="ttl", ttl=300)
        assert original.value == "int:1"
        assert original.cache_mode == "permanent"
        assert original.ttl == 0
        assert updated.value == "int:2"
        assert updated.cache_mode == "ttl"
        assert updated.ttl == 300
        assert updated.type == "scalar:int"

    def test_with_updates_preserves_untouched_fields(self):
        now = datetime(2025, 1, 1, tzinfo=timezone.utc)
        original = ObjectMeta(
            type="scalar:int",
            value="int:1",
            cache_mode="permanent",
            ttl=0,
            created_at=now,
        )
        updated = original.with_updates(cache_mode="ttl", ttl=60)
        assert updated.type == "scalar:int"
        assert updated.value == "int:1"
        assert updated.created_at == now
        assert updated.cache_mode == "ttl"
        assert updated.ttl == 60


class TestObjectMetaTouch:
    def test_touch_updates_updated_at(self):
        original = ObjectMeta(type="collection:list", value="raw:1", cache_mode="permanent", ttl=0)
        now = datetime(2025, 6, 15, 10, 30, 0, tzinfo=timezone.utc)
        touched = original.touch(now)
        assert touched.updated_at == now
        assert touched.type == "collection:list"
        assert touched.value == "raw:1"
        assert original.updated_at is None

    def test_touch_with_item_count(self):
        original = ObjectMeta(type="collection:list", value="raw:1", cache_mode="permanent", ttl=0)
        now = datetime(2025, 6, 15, 10, 30, 0, tzinfo=timezone.utc)
        touched = original.touch(now, item_count=5)
        assert touched.updated_at == now
        assert touched.item_count == 5
        assert original.item_count is None

    def test_touch_without_item_count_preserves_existing(self):
        original = ObjectMeta(type="collection:list", value="raw:1", cache_mode="permanent", ttl=0, item_count=3)
        now = datetime(2025, 6, 15, 10, 30, 0, tzinfo=timezone.utc)
        touched = original.touch(now)
        assert touched.item_count == 3


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


class TestObjectMetaExtField:
    def test_ext_defaults_to_empty_dict(self):
        meta = ObjectMeta(type="scalar:int")
        assert meta.ext == {}

    def test_ext_accepts_initial_values(self):
        meta = ObjectMeta(type="scalar:int", ext={"compression": "zstd"})
        assert meta.ext == {"compression": "zstd"}

    def test_ext_in_to_redis_mapping_uses_dollar_prefix(self):
        meta = ObjectMeta(type="scalar:int", ext={"compression": "zstd", "version": "2"})
        mapping = meta.to_redis_mapping()
        assert "$compression" in mapping
        assert mapping["$compression"] == "zstd"
        assert "$version" in mapping
        assert mapping["$version"] == "2"

    def test_ext_empty_dict_not_in_redis_mapping(self):
        meta = ObjectMeta(type="scalar:int")
        mapping = meta.to_redis_mapping()
        assert all(not k.startswith("$") for k in mapping)

    def test_from_redis_parses_dollar_prefix_to_ext(self):
        data = {
            "type": "scalar:int",
            "value": "int:42",
            "$compression": "zstd",
            "$version": "2",
        }
        meta = ObjectMeta.from_redis(data)
        assert meta.ext == {"compression": "zstd", "version": "2"}

    def test_from_redis_without_ext_fields_gives_empty_ext(self):
        data = {"type": "scalar:int", "value": "int:42"}
        meta = ObjectMeta.from_redis(data)
        assert meta.ext == {}

    def test_roundtrip_preserves_ext(self):
        original = ObjectMeta(
            type="scalar:int",
            value="int:42",
            cache_mode="ttl",
            ttl=300,
            ext={"compression": "zstd"},
        )
        mapping = original.to_redis_mapping()
        restored = ObjectMeta.from_redis(mapping)
        assert restored.ext == {"compression": "zstd"}
        assert restored.type == original.type
        assert restored.value == original.value
        assert restored.cache_mode == original.cache_mode
        assert restored.ttl == original.ttl


class TestObjectMetaExtAccessors:
    def test_get_ext_returns_value(self):
        meta = ObjectMeta(type="scalar:int", ext={"compression": "zstd"})
        assert meta.get_ext("compression") == "zstd"

    def test_get_ext_returns_none_for_missing_key(self):
        meta = ObjectMeta(type="scalar:int", ext={})
        assert meta.get_ext("missing") is None

    def test_has_ext_true(self):
        meta = ObjectMeta(type="scalar:int", ext={"compression": "zstd"})
        assert meta.has_ext("compression") is True

    def test_has_ext_false(self):
        meta = ObjectMeta(type="scalar:int", ext={})
        assert meta.has_ext("compression") is False

    def test_ext_keys_returns_all_keys(self):
        meta = ObjectMeta(type="scalar:int", ext={"compression": "zstd", "version": "2"})
        assert sorted(meta.ext_keys()) == ["compression", "version"]

    def test_ext_keys_empty(self):
        meta = ObjectMeta(type="scalar:int", ext={})
        assert meta.ext_keys() == []

    def test_set_ext_adds_key(self):
        meta = ObjectMeta(type="scalar:int")
        meta.set_ext("compression", "zstd")
        assert meta.ext["compression"] == "zstd"

    def test_set_ext_overwrites_existing(self):
        meta = ObjectMeta(type="scalar:int", ext={"compression": "gzip"})
        meta.set_ext("compression", "zstd")
        assert meta.ext["compression"] == "zstd"

    def test_del_ext_removes_key(self):
        meta = ObjectMeta(type="scalar:int", ext={"compression": "zstd", "version": "2"})
        meta.del_ext("compression")
        assert "compression" not in meta.ext
        assert meta.ext == {"version": "2"}

    def test_del_ext_silent_on_missing_key(self):
        meta = ObjectMeta(type="scalar:int", ext={})
        meta.del_ext("missing")

    def test_update_ext_batch_set(self):
        meta = ObjectMeta(type="scalar:int")
        meta.update_ext({"compression": "zstd", "version": "2"})
        assert meta.ext == {"compression": "zstd", "version": "2"}

    def test_update_ext_merges_with_existing(self):
        meta = ObjectMeta(type="scalar:int", ext={"compression": "gzip"})
        meta.update_ext({"compression": "zstd", "version": "2"})
        assert meta.ext == {"compression": "zstd", "version": "2"}


class TestObjectMetaExtWithExistingMethods:
    def test_with_updates_preserves_ext(self):
        original = ObjectMeta(type="scalar:int", ext={"compression": "zstd"})
        updated = original.with_updates(cache_mode="ttl", ttl=300)
        assert updated.ext == {"compression": "zstd"}
        assert updated.cache_mode == "ttl"
        assert updated.ttl == 300

    def test_with_updates_can_replace_ext(self):
        original = ObjectMeta(type="scalar:int", ext={"compression": "zstd"})
        updated = original.with_updates(ext={"version": "2"})
        assert updated.ext == {"version": "2"}

    def test_touch_preserves_ext(self):
        now = datetime(2025, 6, 15, 10, 30, 0, tzinfo=timezone.utc)
        original = ObjectMeta(type="collection:list", value="raw:1", ext={"compression": "zstd"})
        touched = original.touch(now, item_count=5)
        assert touched.ext == {"compression": "zstd"}
        assert touched.updated_at == now
        assert touched.item_count == 5

    def test_save_and_load_roundtrip_with_ext(self):
        r = MagicMock()
        meta = ObjectMeta(
            type="scalar:int",
            value="int:42",
            cache_mode="ttl",
            ttl=300,
            ext={"compression": "zstd", "version": "2"},
        )
        meta.save(r, "gpdc:d:ns:obj:x")
        call_args = r.hset.call_args
        mapping = call_args[1]["mapping"]
        assert mapping["$compression"] == "zstd"
        assert mapping["$version"] == "2"
        restored = ObjectMeta.from_redis(mapping)
        assert restored.ext == {"compression": "zstd", "version": "2"}
