import pytest

from gpdatacached.exceptions import LifecyclePermissionError
from gpdatacached.keys import make_object_key, make_raw_key, make_ref_key
from gpdatacached.meta import ObjectMeta
from gpdatacached.ttl import TTLPolicy


def _setup_parent_and_child(r, domain, ns):
    parent_name = "parent"
    child_name = "~anon:child1"

    parent_key = make_object_key(domain, ns, parent_name)
    parent_raw = make_raw_key(domain, ns, parent_name)
    ObjectMeta(
        type="collection:list",
        value=parent_raw,
        cache_mode="permanent",
        ttl=0,
        owner=parent_name,
    ).save(r, parent_key)
    r.rpush(parent_raw, f"ref:{make_object_key(domain, ns, child_name)}")

    child_key = make_object_key(domain, ns, child_name)
    child_raw = make_raw_key(domain, ns, child_name)
    ObjectMeta(
        type="collection:dict",
        value=child_raw,
        cache_mode="permanent",
        ttl=0,
        owner=parent_name,
    ).save(r, child_key)
    r.sadd(make_ref_key(domain, ns, child_name), parent_name)

    return parent_name, child_name


def test_set_lifecycle_updates_meta_and_redis(domain, ns):
    ns["obj"] = 42
    obj = ns.get_object("obj")
    policy = TTLPolicy(cache_mode="ttl", declared_ttl=60, expire_seconds=60)
    obj.set_lifecycle(policy)
    meta = obj.meta
    assert meta.cache_mode == "ttl"
    assert meta.ttl == 60
    assert domain.redis.ttl(obj._canonical_redis_key()) > 0


def test_set_lifecycle_propagates_to_owned_children(domain, ns, clean_redis):
    r = clean_redis
    d = domain.name
    n = ns.name

    parent_name, child_name = _setup_parent_and_child(r, d, n)

    parent_obj = ns.get_object(parent_name)
    policy = TTLPolicy(cache_mode="ttl", declared_ttl=120, expire_seconds=120)
    parent_obj.set_lifecycle(policy)

    child_key = make_object_key(d, n, child_name)
    child_meta = ObjectMeta.load(r, child_key)
    assert child_meta.cache_mode == "ttl"
    assert child_meta.ttl == 120


def test_take_ownership_requires_reference(domain, ns):
    ns["obj"] = 42
    obj = ns.get_object("obj")
    with pytest.raises(TypeError, match="named reference"):
        obj.take_ownership()


def test_take_ownership_transfers_owner(domain, ns, clean_redis):
    r = clean_redis
    d = domain.name
    n = ns.name

    parent_name, child_name = _setup_parent_and_child(r, d, n)

    ref_name = "myref"
    ref_alias_key = make_object_key(d, n, ref_name)
    child_key = make_object_key(d, n, child_name)
    r.set(ref_alias_key, f"ref:{child_key}")
    r.sadd(make_ref_key(d, n, child_name), ref_name)

    ref_obj = ns.get_object(ref_name)
    assert ref_obj.is_reference
    ref_obj.take_ownership()

    updated_meta = ObjectMeta.load(r, child_key)
    assert updated_meta.owner == ref_obj.canonical_name


def test_cache_mode_setter_checks_owner(domain, ns, clean_redis):
    r = clean_redis
    d = domain.name
    n = ns.name

    parent_name, child_name = _setup_parent_and_child(r, d, n)

    parent_obj = ns.get_object(parent_name)
    child_obj = parent_obj[0]

    with pytest.raises(LifecyclePermissionError):
        child_obj.cache_mode = "ttl"


def test_persist_checks_owner(domain, ns, clean_redis):
    r = clean_redis
    d = domain.name
    n = ns.name

    parent_name, child_name = _setup_parent_and_child(r, d, n)

    parent_obj = ns.get_object(parent_name)
    child_obj = parent_obj[0]

    with pytest.raises(LifecyclePermissionError):
        child_obj.persist()


def test_meta_setter_checks_owner(domain, ns, clean_redis):
    r = clean_redis
    d = domain.name
    n = ns.name

    parent_name, child_name = _setup_parent_and_child(r, d, n)

    parent_obj = ns.get_object(parent_name)
    child_obj = parent_obj[0]

    new_meta = child_obj.meta.model_copy(update={"cache_mode": "ttl", "ttl": 300})

    with pytest.raises(LifecyclePermissionError):
        child_obj.meta = new_meta
