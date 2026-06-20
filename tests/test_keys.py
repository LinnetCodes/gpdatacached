import pytest
from gpdatacached.keys import (
    GLOBAL_META_KEY,
    GLOBAL_DOMAINS_KEY,
    make_domain_meta_key,
    make_domain_namespaces_key,
    make_lock_key,
    make_lock_notify_key,
    make_namespace_meta_key,
    make_object_key,
    make_object_key_prefix,
    make_raw_key,
    make_ref_key,
    uri_to_redis_key,
    make_uri,
    validate_object_name,
    validate_domain_name,
    validate_namespace_name,
)


class TestConstants:
    def test_global_meta_key(self):
        assert GLOBAL_META_KEY == "gpdc:__meta__"

    def test_global_domains_key(self):
        assert GLOBAL_DOMAINS_KEY == "gpdc:__domains__"


class TestKeyBuilders:
    def test_domain_meta_key(self):
        assert make_domain_meta_key("mydomain") == "gpdc:mydomain:__meta__"

    def test_domain_namespaces_key(self):
        assert make_domain_namespaces_key("mydomain") == "gpdc:mydomain:__namespaces__"

    def test_namespace_meta_key(self):
        assert make_namespace_meta_key("mydomain", "myns") == "gpdc:mydomain:myns:__meta__"

    def test_object_key(self):
        assert make_object_key("mydomain", "myns", "myobj") == "gpdc:mydomain:myns:obj:myobj"

    def test_object_key_prefix(self):
        assert make_object_key_prefix("mydomain", "myns") == "gpdc:mydomain:myns:obj:"

    def test_raw_key(self):
        assert make_raw_key("mydomain", "myns", "myobj") == "gpdc:mydomain:myns:raw:myobj"

    def test_ref_key(self):
        assert make_ref_key("mydomain", "myns", "myobj") == "gpdc:mydomain:myns:ref:myobj"


class TestURIUtils:
    def test_make_uri(self):
        assert make_uri("mydomain", "myns", "myobj") == "gpdc://mydomain/myns/myobj"

    def test_uri_to_redis_key(self):
        result = uri_to_redis_key("gpdc://mydomain/myns/myobj")
        assert result == "gpdc:mydomain:myns:obj:myobj"

    def test_uri_to_redis_key_invalid_scheme(self):
        with pytest.raises(ValueError, match="Invalid GPDC URI"):
            uri_to_redis_key("http://mydomain/myns/myobj")

    def test_uri_to_redis_key_wrong_parts(self):
        with pytest.raises(ValueError, match="Invalid GPDC URI"):
            uri_to_redis_key("gpdc://mydomain/myns")

    def test_roundtrip_uri_to_key(self):
        uri = make_uri("d", "ns", "obj")
        assert uri_to_redis_key(uri) == make_object_key("d", "ns", "obj")

    def test_uri_to_redis_key_empty_domain(self):
        with pytest.raises(ValueError):
            uri_to_redis_key("gpdc:///myns/myobj")

    def test_uri_to_redis_key_reserved_name(self):
        with pytest.raises(ValueError):
            uri_to_redis_key("gpdc://domain/ns/__meta__")


class TestValidateObjectName:
    def test_valid_names(self):
        for name in ["myobj", "my_obj", "my.obj", "obj123", "A1.B2_C3"]:
            validate_object_name(name)  # Should not raise

    def test_invalid_name_colon(self):
        with pytest.raises(ValueError):
            validate_object_name("my:obj")

    def test_invalid_name_space(self):
        with pytest.raises(ValueError):
            validate_object_name("my obj")

    def test_invalid_name_slash(self):
        with pytest.raises(ValueError):
            validate_object_name("my/obj")

    def test_reserved_double_underscore(self):
        with pytest.raises(ValueError):
            validate_object_name("__meta__")

    def test_reserved_double_underscore_custom(self):
        with pytest.raises(ValueError):
            validate_object_name("__anything__")

    def test_valid_single_underscore(self):
        validate_object_name("_meta")  # Single underscore OK

    def test_valid_trailing_single_underscore(self):
        validate_object_name("meta_")  # Trailing single underscore OK


class TestValidateDomainAndNamespace:
    def test_valid_domain(self):
        validate_domain_name("mydomain")

    def test_invalid_domain_colon(self):
        with pytest.raises(ValueError):
            validate_domain_name("my:domain")

    def test_valid_namespace(self):
        validate_namespace_name("myns")

    def test_invalid_namespace_reserved(self):
        with pytest.raises(ValueError):
            validate_namespace_name("__meta__")

    def test_invalid_domain_reserved(self):
        with pytest.raises(ValueError):
            validate_domain_name("__meta__")


class TestKeysErrorPaths:
    def test_uri_invalid_prefix(self):
        with pytest.raises(ValueError, match="must start with"):
            uri_to_redis_key("http://domain/ns/name")

    def test_uri_invalid_format(self):
        with pytest.raises(ValueError, match="must be gpdc://"):
            uri_to_redis_key("gpdc://domain/name")

    def test_validate_reserved_name(self):
        with pytest.raises(ValueError, match="reserved"):
            validate_object_name("__meta__")

    def test_validate_invalid_chars(self):
        with pytest.raises(ValueError, match="only letters"):
            validate_object_name("has spaces")


class TestCollectRefNamesFromRaw:
    def test_scans_hash_raw_key(self, raw_redis):
        from gpdatacached.keys import collect_ref_names_from_raw

        raw_key = "gpdc:test:d:ns:raw:testobj"
        ref_obj_key = "gpdc:test:d:ns:obj:~anon:abc"
        raw_redis.hset(raw_key, mapping={"str:k1": f"ref:{ref_obj_key}", "str:k2": "str:hello"})
        try:
            names = collect_ref_names_from_raw(raw_redis, raw_key, "collection:unknown")
            assert "~anon:abc" in names
        finally:
            raw_redis.delete(raw_key)

    def test_scans_list_raw_key(self, raw_redis):
        from gpdatacached.keys import collect_ref_names_from_raw

        raw_key = "gpdc:test:d:ns:raw:testlist"
        ref_obj_key = "gpdc:test:d:ns:obj:~anon:xyz"
        raw_redis.rpush(raw_key, f"ref:{ref_obj_key}", "int:42")
        try:
            names = collect_ref_names_from_raw(raw_redis, raw_key, "collection:unknown")
            assert "~anon:xyz" in names
        finally:
            raw_redis.delete(raw_key)

    def test_empty_raw_key(self, raw_redis):
        from gpdatacached.keys import collect_ref_names_from_raw

        names = collect_ref_names_from_raw(raw_redis, "", "collection:dict")
        assert names == []

    def test_set_raw_key_not_scanned(self, raw_redis):
        from gpdatacached.keys import collect_ref_names_from_raw

        raw_key = "gpdc:test:d:ns:raw:testset"
        ref_obj_key = "gpdc:test:d:ns:obj:~anon:setref"
        raw_redis.sadd(raw_key, f"ref:{ref_obj_key}")
        try:
            names = collect_ref_names_from_raw(raw_redis, raw_key, "collection:set")
            assert names == []
        finally:
            raw_redis.delete(raw_key)


class TestParseRefEncoded:
    def test_valid_ref(self):
        from gpdatacached.keys import parse_ref_encoded

        result = parse_ref_encoded("ref:gpdc:domain:ns:obj:target_name")
        assert result == "target_name"

    def test_non_ref_returns_none(self):
        from gpdatacached.keys import parse_ref_encoded

        assert parse_ref_encoded("str:hello") is None

    def test_malformed_ref_returns_none(self):
        from gpdatacached.keys import parse_ref_encoded

        assert parse_ref_encoded("ref:nocolonshere") is None

    def test_anonymous_name(self):
        from gpdatacached.keys import parse_ref_encoded

        result = parse_ref_encoded("ref:gpdc:d:ns:obj:~anon:abc-123")
        assert result == "~anon:abc-123"

    def test_empty_string(self):
        from gpdatacached.keys import parse_ref_encoded

        assert parse_ref_encoded("") is None


class TestLockKeyBuilders:
    def test_lock_key(self):
        assert make_lock_key("mydomain", "myns", "myobj") == "gpdc:mydomain:myns:lock:myobj"

    def test_lock_notify_key(self):
        assert make_lock_notify_key("mydomain", "myns", "myobj") == "gpdc:mydomain:myns:locknotify:myobj"
