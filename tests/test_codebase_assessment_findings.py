"""Failing tests derived from the 2026-05-18 codebase assessment findings.

These tests intentionally encode the desired post-refactor behavior.
They are expected to fail against the current implementation.
"""

from __future__ import annotations

import pytest

from gpdatacached.codec import BoolCodec
from gpdatacached.config import GPDCDomainConfig
from gpdatacached.domain import GPDCDomain
from gpdatacached.keys import make_object_key, make_raw_key, make_ref_key
from gpdatacached.scalar import GPDCScalarObject
from gpdatacached.types import GPDCTypeRegistry


@pytest.fixture
def isolated_domain(clean_redis, domain_config):
    domain_config.gc_enabled = False
    with GPDCDomain("testdomain", domain_config) as domain:
        yield domain


@pytest.fixture
def isolated_ns(isolated_domain):
    return isolated_domain.get_namespace("testns")


def test_nested_container_replacement_is_rejected(isolated_ns, clean_redis):
    isolated_ns["d"] = {"a": [1, 2, 3]}
    anon_name = isolated_ns["d"]["a"].name

    with pytest.raises(TypeError, match="Cannot overwrite an existing container slot"):
        isolated_ns["d"]["a"] = "replaced"

    assert list(isolated_ns["d"]["a"]) == [1, 2, 3]
    assert clean_redis.exists(make_ref_key("testdomain", "testns", anon_name))
    assert clean_redis.exists(make_object_key("testdomain", "testns", anon_name))
    assert clean_redis.exists(make_raw_key("testdomain", "testns", anon_name))


@pytest.mark.skip(reason="Pending refactor")
def test_overwriting_container_owner_is_rejected(isolated_ns, clean_redis):
    isolated_ns["x"] = {"a": [1, 2, 3]}
    anon_name = isolated_ns["x"]["a"].name

    with pytest.raises(TypeError, match="Cannot reassign a namespace container owner"):
        isolated_ns["x"] = 1

    assert isolated_ns["x"]["a"][0] == 1
    assert clean_redis.hget(make_object_key("testdomain", "testns", "x"), "binding_kind") == "owner"
    assert clean_redis.exists(make_raw_key("testdomain", "testns", "x"))
    assert clean_redis.exists(make_ref_key("testdomain", "testns", anon_name))
    assert clean_redis.exists(make_object_key("testdomain", "testns", anon_name))
    assert clean_redis.exists(make_raw_key("testdomain", "testns", anon_name))


def test_nested_cross_namespace_container_assignment_is_rejected(isolated_domain):
    ns1 = isolated_domain.get_namespace("ns1")
    ns2 = isolated_domain.get_namespace("ns2")
    ns1["lst"] = [1, 2, 3]
    ns2["holder"] = {}

    with pytest.raises(ValueError, match="Cross-namespace"):
        ns2["holder"]["foreign"] = ns1["lst"]


@pytest.mark.skip(reason="Pending refactor")
def test_alias_object_applies_real_redis_expiry_when_source_uses_ttl(isolated_ns, clean_redis):
    isolated_ns["a"] = [1, 2, 3]
    obj = isolated_ns.get_object("a")
    obj.cache_mode = "ttl"
    obj.ttl = 60

    isolated_ns["b"] = isolated_ns["a"]

    source_key = make_object_key("testdomain", "testns", "a")
    alias_key = make_object_key("testdomain", "testns", "b")
    source_remaining = clean_redis.ttl(source_key)
    alias_remaining = clean_redis.ttl(alias_key)

    assert source_remaining > 0
    assert alias_remaining > 0
    assert alias_remaining <= source_remaining


def test_scalar_object_rejects_value_of_different_declared_type(isolated_ns):
    isolated_ns["x"] = 1
    obj = isolated_ns.get_object("x")
    assert obj.value == 1

    with pytest.raises(TypeError):
        obj.value = "abc"

    assert obj.value == 1


def test_registered_custom_scalar_type_round_trips_through_namespace(isolated_ns, monkeypatch):
    monkeypatch.setattr(GPDCTypeRegistry, "_by_gpdc_type", dict(GPDCTypeRegistry._by_gpdc_type))
    monkeypatch.setattr(GPDCTypeRegistry, "_by_python_type", list(GPDCTypeRegistry._by_python_type))

    class Blob:
        def __init__(self, payload: str):
            self.payload = payload

        def __eq__(self, other):
            return isinstance(other, Blob) and self.payload == other.payload

    class BlobObject(GPDCScalarObject):
        TYPE = "scalar:blob"

    class BlobCodec:
        @classmethod
        def encode_value(cls, value: Blob) -> str:
            return f"blob:{value.payload}"

        @classmethod
        def decode_value(cls, encoded: str) -> Blob:
            prefix = "blob:"
            if not encoded.startswith(prefix):
                raise ValueError(encoded)
            return Blob(encoded[len(prefix) :])

    GPDCTypeRegistry.register(
        python_type=Blob,
        gpdc_type="scalar:blob",
        object_class=BlobObject,
        codec_class=BlobCodec,
        priority=5,
    )

    value = Blob("hello")
    isolated_ns["blob"] = value
    assert isolated_ns["blob"] == value


def test_domain_config_rejects_invalid_cache_mode_and_negative_ttl():
    missing_validations = []

    try:
        GPDCDomainConfig(default_cache_mode="bad")
    except ValueError:
        pass
    else:
        missing_validations.append("default_cache_mode")

    try:
        GPDCDomainConfig(default_ttl_seconds=-1)
    except ValueError:
        pass
    else:
        missing_validations.append("default_ttl_seconds")

    assert not missing_validations, f"Missing config validation for: {', '.join(missing_validations)}"


def test_namespace_contains_excludes_anonymous_objects(isolated_ns):
    isolated_ns["holder"] = {"nested": [1, 2, 3]}
    anon_name = isolated_ns["holder"]["nested"].name

    assert anon_name not in isolated_ns


def test_decode_invalid_bool_payload_raises_value_error():
    with pytest.raises(ValueError):
        BoolCodec.decode_value("bool:not-bool")


@pytest.mark.skip(reason="Pending refactor")
def test_alias_metadata_matches_source_declared_ttl(isolated_ns, clean_redis):
    isolated_ns["src"] = [1, 2, 3]
    src = isolated_ns.get_object("src")
    src.cache_mode = "ttl"
    src.ttl = 300
    isolated_ns["alias"] = isolated_ns["src"]

    src_key = make_object_key("testdomain", "testns", "src")
    alias_key = make_object_key("testdomain", "testns", "alias")
    src_meta = clean_redis.hgetall(src_key)
    alias_meta = clean_redis.hgetall(alias_key)

    assert alias_meta["cache_mode"] == src_meta["cache_mode"]
    assert alias_meta["ttl"] == src_meta["ttl"]
    assert clean_redis.ttl(alias_key) <= clean_redis.ttl(src_key)


def test_anonymous_object_inherits_declared_policy_not_remaining(isolated_ns, clean_redis):
    from gpdatacached.config import GPDCNamespaceConfig
    from gpdatacached.namespace import GPDCNamespace

    cfg = GPDCNamespaceConfig(default_cache_mode="ttl", default_ttl_seconds=600)
    ns = GPDCNamespace("ttl_ns", isolated_ns.domain, config=cfg)
    ns["holder"] = {"nested": [1, 2, 3]}

    nested_obj = ns["holder"]["nested"]
    anon_key = make_object_key("testdomain", "ttl_ns", nested_obj.name)

    anon_meta = clean_redis.hgetall(anon_key)
    assert anon_meta["cache_mode"] == "ttl"
    assert anon_meta["ttl"] == "600"

    anon_remaining = clean_redis.ttl(anon_key)
    assert anon_remaining > 0
    assert anon_remaining <= 600


def test_sliding_touch_resets_expiry_to_declared_ttl(isolated_ns, clean_redis):
    isolated_ns["x"] = [1, 2, 3]
    obj = isolated_ns.get_object("x")
    obj.cache_mode = "sliding"
    obj.ttl = 300

    key = make_object_key("testdomain", "testns", "x")
    import time

    time.sleep(1)

    obj.append(4)

    remaining = clean_redis.ttl(key)
    assert remaining > 290


def test_permanent_object_has_no_redis_expiry(isolated_ns, clean_redis):
    isolated_ns["perm"] = 42
    key = make_object_key("testdomain", "testns", "perm")
    assert clean_redis.ttl(key) == -1
