import time
from unittest.mock import MagicMock

import pytest

from gpdatacached.config import GPDCDomainConfig
from gpdatacached.domain import GPDCDomain
from gpdatacached.keys import make_object_key, make_raw_key, make_ref_key
from gpdatacached.meta import ObjectMeta
from gpdatacached.ttl import (
    TTLPolicy,
    policy_from_defaults,
    policy_from_meta,
    policy_for_cache_mode_change,
    policy_for_ttl_change,
    policy_for_sliding_touch,
    apply_policy,
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
        p = policy_from_meta(ObjectMeta(type="scalar:int", cache_mode="permanent", ttl=0))
        assert p.cache_mode == "permanent"
        assert p.declared_ttl == 0
        assert p.expire_seconds is None

    def test_ttl_meta(self):
        p = policy_from_meta(ObjectMeta(type="scalar:int", cache_mode="ttl", ttl=300))
        assert p.cache_mode == "ttl"
        assert p.declared_ttl == 300
        assert p.expire_seconds == 300

    def test_sliding_meta(self):
        p = policy_from_meta(ObjectMeta(type="scalar:int", cache_mode="sliding", ttl=60))
        assert p.expire_seconds == 60

    def test_empty_meta_defaults_to_permanent(self):
        p = policy_from_meta(ObjectMeta(type="scalar:int", cache_mode="permanent", ttl=0))
        assert p.cache_mode == "permanent"
        assert p.declared_ttl == 0
        assert p.expire_seconds is None

    def test_ttl_meta_with_zero_ttl(self):
        p = policy_from_meta(ObjectMeta(type="scalar:int", cache_mode="ttl", ttl=0))
        assert p.expire_seconds is None

    def test_ttl_meta_with_missing_ttl(self):
        p = policy_from_meta(ObjectMeta(type="scalar:int", cache_mode="ttl", ttl=0))
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


class TestPolicyFromMetaInherited:
    def test_inherited_meta_falls_back_to_permanent(self):
        p = policy_from_meta(ObjectMeta(type="collection:list", cache_mode="permanent", ttl=300))
        assert p.cache_mode == "permanent"
        assert p.declared_ttl == 300

    def test_inherited_meta_no_expire_when_permanent(self):
        p = policy_from_meta(ObjectMeta(type="collection:list", cache_mode="permanent", ttl=300))
        assert p.expire_seconds is None

    def test_zero_ttl_permanent_no_expire(self):
        p = policy_from_meta(ObjectMeta(type="collection:list", cache_mode="permanent", ttl=0))
        assert p.cache_mode == "permanent"
        assert p.declared_ttl == 0
        assert p.expire_seconds is None


class TestPolicyFromDefaultsRejectsInherited:
    def test_inherited_rejected_in_defaults(self):
        with pytest.raises(ValueError, match="Invalid cache_mode"):
            policy_from_defaults("inherited", 300)


class TestPolicyForCacheModeChangeRejectsInherited:
    def test_change_to_inherited_rejected(self):
        current = TTLPolicy(cache_mode="ttl", declared_ttl=300, expire_seconds=300)
        with pytest.raises(ValueError, match="Invalid cache_mode"):
            policy_for_cache_mode_change(current, "inherited")


def test_effective_policy_removed():
    import gpdatacached.ttl as ttl_mod

    assert not hasattr(ttl_mod, "effective_policy")


def test_validate_policy_rejects_invalid():
    from gpdatacached.ttl import _validate_policy, TTLPolicy

    with pytest.raises(ValueError):
        _validate_policy(TTLPolicy(cache_mode="invalid", declared_ttl=0, expire_seconds=None))


def test_validate_policy_rejects_negative_ttl():
    from gpdatacached.ttl import _validate_policy, TTLPolicy

    with pytest.raises(ValueError):
        _validate_policy(TTLPolicy(cache_mode="ttl", declared_ttl=-1, expire_seconds=1))


def test_propagate_lifecycle_uses_owner_check(clean_redis):
    from gpdatacached.ttl import propagate_lifecycle, TTLPolicy

    r = clean_redis
    parent_name = "parent"
    child_owned = "~anon:owned"
    child_not_owned = "~anon:free"

    parent_key = make_object_key("d", "ns", parent_name)
    parent_raw = make_raw_key("d", "ns", parent_name)
    parent_meta = ObjectMeta(
        type="collection:list",
        value=parent_raw,
        cache_mode="ttl",
        ttl=60,
        owner="parent",
    )
    parent_meta.save(r, parent_key)
    r.rpush(
        parent_raw,
        f"ref:{make_object_key('d', 'ns', child_owned)}",
        f"ref:{make_object_key('d', 'ns', child_not_owned)}",
    )

    owned_key = make_object_key("d", "ns", child_owned)
    owned_raw = make_raw_key("d", "ns", child_owned)
    owned_meta = ObjectMeta(
        type="collection:dict",
        value=owned_raw,
        cache_mode="ttl",
        ttl=30,
        owner="parent",
    )
    owned_meta.save(r, owned_key)

    not_owned_key = make_object_key("d", "ns", child_not_owned)
    not_owned_raw = make_raw_key("d", "ns", child_not_owned)
    not_owned_meta = ObjectMeta(
        type="collection:dict",
        value=not_owned_raw,
        cache_mode="ttl",
        ttl=30,
        owner="other_obj",
    )
    not_owned_meta.save(r, not_owned_key)

    policy = TTLPolicy(cache_mode="ttl", declared_ttl=120, expire_seconds=120)
    propagate_lifecycle(r, "d", "ns", parent_name, policy, set())

    updated_owned = ObjectMeta.load(r, owned_key)
    assert updated_owned.ttl == 120
    assert updated_owned.owner == "parent"

    updated_not_owned = ObjectMeta.load(r, not_owned_key)
    assert updated_not_owned.ttl == 30
    assert updated_not_owned.owner == "other_obj"


@pytest.fixture
def ttl_domain(clean_redis):
    cfg = GPDCDomainConfig(
        redis_host="127.0.0.1",
        redis_port=6379,
        redis_db=1,
        redis_password="123456",
        gc_enabled=False,
    )
    d = GPDCDomain("testdomain_ttl", cfg)
    yield d
    d.close()


@pytest.fixture
def gc_disabled_domain(clean_redis):
    cfg = GPDCDomainConfig(
        redis_host="127.0.0.1",
        redis_port=6379,
        redis_db=1,
        redis_password="123456",
        gc_enabled=False,
    )
    d = GPDCDomain("testdomain", cfg)
    yield d
    d.close()


_INHERIT_NS = "testns_inherit"


def _make_namespace(r, domain_name, cache_mode="permanent", ttl=0):
    config = GPDCDomainConfig(
        redis_host="127.0.0.1",
        redis_port=6379,
        redis_db=1,
        redis_password="123456",
        gc_enabled=False,
        default_cache_mode=cache_mode,
        default_ttl_seconds=ttl,
    )
    domain = GPDCDomain(domain_name, config)
    return domain.get_namespace(_INHERIT_NS)


def _extract_anon_name_from_dict(r, domain, ns, parent, field):
    raw_data = r.hgetall(make_raw_key(domain, ns, parent))
    encoded_val = raw_data.get(f"str:{field}", "")
    assert encoded_val.startswith("ref:gpdc:"), f"Expected ref, got: {encoded_val}"
    return encoded_val.split(":obj:")[1]


class TestTTLExpiry:
    def test_ttl_object_disappears_after_expiry(self, ttl_domain):
        ns = ttl_domain.get_namespace("testns_ttl_expiry")

        ns["x"] = 42
        obj = ns.get_object("x")
        obj.cache_mode = "ttl"
        obj.ttl = 1

        assert "x" in ns
        assert ns["x"] == 42

        time.sleep(1.1)

        assert "x" not in ns
        with pytest.raises(KeyError):
            _ = ns["x"]
        assert ns.get("x") is None

    def test_sliding_object_disappears_after_expiry(self, ttl_domain):
        ns = ttl_domain.get_namespace("testns_ttl_expiry")

        ns["y"] = "hello"
        obj = ns.get_object("y")
        obj.cache_mode = "sliding"
        obj.ttl = 1

        assert "y" in ns

        time.sleep(1.1)

        assert "y" not in ns
        with pytest.raises(KeyError):
            _ = ns["y"]

    def test_ttl_container_disappears_after_expiry(self, ttl_domain):
        ns = ttl_domain.get_namespace("testns_ttl_expiry")

        ns["mylist"] = [1, 2, 3]
        obj = ns.get_object("mylist")
        obj.cache_mode = "ttl"
        obj.ttl = 1

        assert "mylist" in ns

        time.sleep(1.1)

        assert "mylist" not in ns
        with pytest.raises(KeyError):
            _ = ns["mylist"]


class TestAnonymousInherited:
    def test_anon_object_has_inherited_cache_mode(self, gc_disabled_domain):
        d = gc_disabled_domain
        ns = d.get_namespace(_INHERIT_NS)
        r = d._redis
        domain_name = "testdomain"

        ns["d"] = {"a": [1, 2, 3]}
        anon_name = _extract_anon_name_from_dict(r, domain_name, _INHERIT_NS, "d", "a")
        anon_obj_key = make_object_key(domain_name, _INHERIT_NS, anon_name)
        assert r.hget(anon_obj_key, "cache_mode") == "permanent"
        assert r.hget(anon_obj_key, "owner") == "d"

    def test_anon_raw_key_has_ttl(self, clean_redis):
        r = clean_redis
        domain_name = "testdomain"
        ns = _make_namespace(r, domain_name, cache_mode="ttl", ttl=300)
        ns["d"] = {"a": [1, 2, 3]}
        anon_name = _extract_anon_name_from_dict(r, domain_name, _INHERIT_NS, "d", "a")
        anon_raw_key = make_raw_key(domain_name, _INHERIT_NS, anon_name)
        ttl_val = r.ttl(anon_raw_key)
        assert ttl_val > 0

    def test_anon_ref_key_has_ttl(self, clean_redis):
        r = clean_redis
        domain_name = "testdomain"
        ns = _make_namespace(r, domain_name, cache_mode="ttl", ttl=300)
        ns["d"] = {"a": [1, 2, 3]}
        anon_name = _extract_anon_name_from_dict(r, domain_name, _INHERIT_NS, "d", "a")
        anon_ref_key = make_ref_key(domain_name, _INHERIT_NS, anon_name)
        ttl_val = r.ttl(anon_ref_key)
        assert ttl_val > 0


class TestSlidingNestedContainer:
    def test_sliding_parent_refreshes_nested_anon_ttl(self, clean_redis):
        r = clean_redis
        domain_name = "testdomain_slide"
        ns = _make_namespace(r, domain_name, cache_mode="sliding", ttl=10)
        ns["d"] = {"items": [1, 2, 3]}
        anon_name = _extract_anon_name_from_dict(r, domain_name, _INHERIT_NS, "d", "items")
        anon_obj_key = make_object_key(domain_name, _INHERIT_NS, anon_name)
        assert r.hget(anon_obj_key, "cache_mode") == "sliding"
        assert r.hget(anon_obj_key, "owner") == "d"
        anon_raw_key = make_raw_key(domain_name, _INHERIT_NS, anon_name)
        initial_ttl = r.ttl(anon_raw_key)
        assert initial_ttl > 0

        time.sleep(1)

        d = ns["d"]
        d["b"] = "hello"

        refreshed_ttl = r.ttl(anon_raw_key)
        assert refreshed_ttl > 0


class TestTTLExpiryConsistency:
    def test_ttl_container_obj_has_ttl(self, clean_redis):
        r = clean_redis
        domain_name = "testdomain_ttl"
        ns = _make_namespace(r, domain_name, cache_mode="ttl", ttl=300)
        ns["lst"] = [1, 2, 3]
        obj_key = make_object_key(domain_name, _INHERIT_NS, "lst")
        assert r.ttl(obj_key) > 0

    def test_ttl_named_container_raw_has_ttl(self, clean_redis):
        r = clean_redis
        domain_name = "testdomain_ttl"
        ns = _make_namespace(r, domain_name, cache_mode="ttl", ttl=300)
        ns["lst"] = [1, 2, 3]
        raw_key = make_raw_key(domain_name, _INHERIT_NS, "lst")
        assert r.ttl(raw_key) > 0

    def test_ttl_container_with_nested_has_ttl_on_all_keys(self, clean_redis):
        r = clean_redis
        domain_name = "testdomain_ttl2"
        ns = _make_namespace(r, domain_name, cache_mode="ttl", ttl=300)
        ns["d"] = {"nested": [10, 20], "x": 1}
        anon_name = _extract_anon_name_from_dict(r, domain_name, _INHERIT_NS, "d", "nested")
        parent_obj_key = make_object_key(domain_name, _INHERIT_NS, "d")
        assert r.ttl(parent_obj_key) > 0

        anon_obj_key = make_object_key(domain_name, _INHERIT_NS, anon_name)
        anon_raw_key = make_raw_key(domain_name, _INHERIT_NS, anon_name)
        anon_ref_key = make_ref_key(domain_name, _INHERIT_NS, anon_name)
        assert r.ttl(anon_obj_key) > 0
        assert r.ttl(anon_raw_key) > 0
        assert r.ttl(anon_ref_key) > 0


class TestInheritedOverride:
    def test_anon_cache_mode_requires_owner_permission(self, gc_disabled_domain):
        d = gc_disabled_domain
        ns = d.get_namespace(_INHERIT_NS)

        ns["d"] = {"a": [1, 2, 3]}
        inner = ns["d"]["a"]
        from gpdatacached.exceptions import LifecyclePermissionError

        with pytest.raises(LifecyclePermissionError):
            inner.cache_mode = "ttl"

    def test_anon_ttl_requires_owner_permission(self, gc_disabled_domain):
        d = gc_disabled_domain
        ns = d.get_namespace(_INHERIT_NS)

        ns["d"] = {"a": [1, 2, 3]}
        inner = ns["d"]["a"]
        from gpdatacached.exceptions import LifecyclePermissionError

        with pytest.raises(LifecyclePermissionError):
            inner.ttl = 600


class TestPermanentContainerLifecycle:
    def test_permanent_container_raw_is_persistent(self, gc_disabled_domain):
        d = gc_disabled_domain
        ns = d.get_namespace(_INHERIT_NS)
        r = d._redis
        domain_name = "testdomain"

        ns["lst"] = [1, 2, 3]
        raw_key = make_raw_key(domain_name, _INHERIT_NS, "lst")
        assert r.ttl(raw_key) == -1
