# tests/test_ttl_policy.py
import pytest
from unittest.mock import MagicMock

from gpdatacached.ttl import (
    TTLPolicy,
    policy_from_defaults,
    policy_from_meta,
    policy_for_cache_mode_change,
    policy_for_ttl_change,
    policy_for_sliding_touch,
    apply_policy,
    meta_ttl_value,
)


class TestPolicyFromDefaults:
    def test_permanent_zero_ttl(self):
        p = policy_from_defaults("permanent", 0)
        assert p.cache_mode == "permanent"
        assert p.declared_ttl == 0
        assert p.expire_seconds is None

    def test_permanent_positive_ttl(self):
        p = policy_from_defaults("permanent", 300)
        assert p.expire_seconds is None

    def test_ttl_with_positive_ttl(self):
        p = policy_from_defaults("ttl", 300)
        assert p.cache_mode == "ttl"
        assert p.declared_ttl == 300
        assert p.expire_seconds == 300

    def test_ttl_with_zero_ttl(self):
        p = policy_from_defaults("ttl", 0)
        assert p.expire_seconds is None

    def test_sliding_with_positive_ttl(self):
        p = policy_from_defaults("sliding", 60)
        assert p.cache_mode == "sliding"
        assert p.declared_ttl == 60
        assert p.expire_seconds == 60

    def test_sliding_with_zero_ttl(self):
        p = policy_from_defaults("sliding", 0)
        assert p.expire_seconds is None

    def test_invalid_cache_mode_raises(self):
        with pytest.raises(ValueError, match="Invalid cache_mode"):
            policy_from_defaults("bogus", 0)


class TestPolicyFromMeta:
    def test_permanent_meta(self):
        p = policy_from_meta({"cache_mode": "permanent", "ttl": ""})
        assert p.cache_mode == "permanent"
        assert p.declared_ttl == 0
        assert p.expire_seconds is None

    def test_ttl_meta(self):
        p = policy_from_meta({"cache_mode": "ttl", "ttl": "300"})
        assert p.cache_mode == "ttl"
        assert p.declared_ttl == 300
        assert p.expire_seconds == 300

    def test_sliding_meta(self):
        p = policy_from_meta({"cache_mode": "sliding", "ttl": "60"})
        assert p.expire_seconds == 60

    def test_empty_meta_defaults_to_permanent(self):
        p = policy_from_meta({})
        assert p.cache_mode == "permanent"
        assert p.declared_ttl == 0
        assert p.expire_seconds is None

    def test_ttl_meta_with_zero_ttl(self):
        p = policy_from_meta({"cache_mode": "ttl", "ttl": "0"})
        assert p.expire_seconds is None

    def test_ttl_meta_with_missing_ttl(self):
        p = policy_from_meta({"cache_mode": "ttl"})
        assert p.declared_ttl == 0
        assert p.expire_seconds is None


class TestPolicyForCacheModeChange:
    def test_change_to_permanent(self):
        current = TTLPolicy(cache_mode="ttl", declared_ttl=300, expire_seconds=300)
        p = policy_for_cache_mode_change(current, "permanent")
        assert p.cache_mode == "permanent"
        assert p.declared_ttl == 300
        assert p.expire_seconds is None

    def test_change_to_ttl_with_declared_ttl(self):
        current = TTLPolicy(cache_mode="permanent", declared_ttl=300, expire_seconds=None)
        p = policy_for_cache_mode_change(current, "ttl")
        assert p.cache_mode == "ttl"
        assert p.expire_seconds == 300

    def test_change_to_sliding_with_zero_declared(self):
        current = TTLPolicy(cache_mode="permanent", declared_ttl=0, expire_seconds=None)
        p = policy_for_cache_mode_change(current, "sliding")
        assert p.expire_seconds is None

    def test_invalid_mode_raises(self):
        current = TTLPolicy(cache_mode="permanent", declared_ttl=0, expire_seconds=None)
        with pytest.raises(ValueError, match="Invalid cache_mode"):
            policy_for_cache_mode_change(current, "bad")


class TestPolicyForTtlChange:
    def test_change_ttl_in_ttl_mode(self):
        current = TTLPolicy(cache_mode="ttl", declared_ttl=300, expire_seconds=300)
        p = policy_for_ttl_change(current, 600)
        assert p.declared_ttl == 600
        assert p.expire_seconds == 600

    def test_change_ttl_in_permanent_mode_no_expire(self):
        current = TTLPolicy(cache_mode="permanent", declared_ttl=0, expire_seconds=None)
        p = policy_for_ttl_change(current, 300)
        assert p.declared_ttl == 300
        assert p.expire_seconds is None

    def test_change_to_zero_in_ttl_mode(self):
        current = TTLPolicy(cache_mode="ttl", declared_ttl=300, expire_seconds=300)
        p = policy_for_ttl_change(current, 0)
        assert p.declared_ttl == 0
        assert p.expire_seconds is None

    def test_change_ttl_in_sliding_mode(self):
        current = TTLPolicy(cache_mode="sliding", declared_ttl=60, expire_seconds=10)
        p = policy_for_ttl_change(current, 120)
        assert p.declared_ttl == 120
        assert p.expire_seconds == 120


class TestPolicyForSlidingTouch:
    def test_resets_expire_to_declared_ttl(self):
        current = TTLPolicy(cache_mode="sliding", declared_ttl=60, expire_seconds=10)
        p = policy_for_sliding_touch(current)
        assert p.expire_seconds == 60

    def test_no_touch_when_zero_ttl(self):
        current = TTLPolicy(cache_mode="sliding", declared_ttl=0, expire_seconds=None)
        p = policy_for_sliding_touch(current)
        assert p.expire_seconds is None

    def test_no_touch_for_ttl_mode(self):
        current = TTLPolicy(cache_mode="ttl", declared_ttl=60, expire_seconds=10)
        p = policy_for_sliding_touch(current)
        assert p.expire_seconds == 10

    def test_no_touch_for_permanent_mode(self):
        current = TTLPolicy(cache_mode="permanent", declared_ttl=0, expire_seconds=None)
        p = policy_for_sliding_touch(current)
        assert p.expire_seconds is None


class TestApplyPolicy:
    def test_persist_called_when_expire_seconds_is_none(self):
        r = MagicMock()
        p = TTLPolicy(cache_mode="permanent", declared_ttl=0, expire_seconds=None)
        apply_policy(r, "testkey", p)
        r.persist.assert_called_once_with("testkey")
        r.expire.assert_not_called()

    def test_expire_called_when_expire_seconds_set(self):
        r = MagicMock()
        p = TTLPolicy(cache_mode="ttl", declared_ttl=300, expire_seconds=300)
        apply_policy(r, "testkey", p)
        r.expire.assert_called_once_with("testkey", 300)
        r.persist.assert_not_called()


class TestMetaTtlValue:
    def test_zero_ttl_returns_empty_string(self):
        p = TTLPolicy(cache_mode="permanent", declared_ttl=0, expire_seconds=None)
        assert meta_ttl_value(p) == ""

    def test_positive_ttl_returns_string(self):
        p = TTLPolicy(cache_mode="ttl", declared_ttl=300, expire_seconds=300)
        assert meta_ttl_value(p) == "300"
