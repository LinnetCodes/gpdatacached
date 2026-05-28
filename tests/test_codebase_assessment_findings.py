"""Failing tests derived from the 2026-05-18 codebase assessment findings.

These tests intentionally encode the desired post-refactor behavior.
They are expected to fail against the current implementation.
"""

from __future__ import annotations

import pytest

from gpdatacached.codec import BoolCodec
from gpdatacached.config import GPDCDomainConfig
from gpdatacached.domain import GPDCDomain
from gpdatacached.exceptions import CanonicalObjectReferencedError
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


def test_overwriting_container_owner_allowed_without_refs(isolated_ns, clean_redis):
    isolated_ns["x"] = [1, 2, 3]
    isolated_ns["x"] = 42
    assert isolated_ns["x"] == 42


def test_overwriting_container_owner_blocked_with_refs(isolated_ns, clean_redis):
    isolated_ns["x"] = [1, 2, 3]
    isolated_ns["y"] = isolated_ns["x"]
    with pytest.raises(CanonicalObjectReferencedError):
        isolated_ns["x"] = 42


def test_nested_cross_namespace_container_assignment_is_rejected(isolated_domain):
    ns1 = isolated_domain.get_namespace("ns1")
    ns2 = isolated_domain.get_namespace("ns2")
    ns1["lst"] = [1, 2, 3]
    ns2["holder"] = {}

    with pytest.raises(ValueError, match="Cross-namespace"):
        ns2["holder"]["foreign"] = ns1["lst"]


def test_alias_lifecycle_delegates_to_canonical_owner(isolated_ns):
    isolated_ns["owner"] = [1, 2, 3]
    isolated_ns.get_object("owner").cache_mode = "ttl"
    isolated_ns.get_object("owner").ttl = 300
    isolated_ns["alias"] = isolated_ns["owner"]
    alias_obj = isolated_ns.get_object("alias")
    owner_obj = isolated_ns.get_object("owner")
    assert alias_obj.cache_mode == "ttl"
    assert alias_obj.ttl == 300
    assert alias_obj.created_at == owner_obj.created_at


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


def test_alias_reads_canonical_ttl(isolated_ns):
    isolated_ns["owner"] = [1, 2, 3]
    isolated_ns.get_object("owner").cache_mode = "ttl"
    isolated_ns.get_object("owner").ttl = 600
    isolated_ns["alias"] = isolated_ns["owner"]
    assert isolated_ns.get_object("alias").ttl == 600
    assert isolated_ns.get_object("alias").cache_mode == "ttl"


def test_anonymous_object_inherits_declared_policy_not_remaining(isolated_ns, clean_redis):
    from gpdatacached.config import GPDCNamespaceConfig
    from gpdatacached.namespace import GPDCNamespace

    cfg = GPDCNamespaceConfig(default_cache_mode="ttl", default_ttl_seconds=600)
    ns = GPDCNamespace("ttl_ns", isolated_ns.domain, config=cfg)
    ns["holder"] = {"nested": [1, 2, 3]}

    nested_obj = ns["holder"]["nested"]
    anon_key = make_object_key("testdomain", "ttl_ns", nested_obj.name)

    anon_meta = clean_redis.hgetall(anon_key)
    assert anon_meta["cache_mode"] == "inherited"
    assert anon_meta["ttl"] == ""

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
