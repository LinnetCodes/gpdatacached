"""Tests for the new alias-as-string binding model."""

import pytest
from gpdatacached.exceptions import CanonicalObjectReferencedError
from gpdatacached.keys import make_object_key, make_ref_key


class TestAliasStorageModel:
    """Verify that aliases are stored as Redis strings, not hashes."""

    def test_owner_stored_as_hash(self, ns):
        ns["mylist"] = [1, 2, 3]
        r = ns._redis()
        obj_key = make_object_key(ns.domain.name, ns.name, "mylist")
        assert r.type(obj_key) == "hash"

    def test_alias_stored_as_string(self, ns):
        ns["owner"] = [10, 20]
        ns["alias"] = ns["owner"]
        r = ns._redis()
        alias_key = make_object_key(ns.domain.name, ns.name, "alias")
        assert r.type(alias_key) == "string"

    def test_alias_string_value_is_ref_to_canonical(self, ns):
        ns["owner"] = [10, 20]
        ns["alias"] = ns["owner"]
        r = ns._redis()
        alias_key = make_object_key(ns.domain.name, ns.name, "alias")
        owner_key = make_object_key(ns.domain.name, ns.name, "owner")
        assert r.get(alias_key) == f"ref:{owner_key}"

    def test_owner_metadata_has_no_binding_kind(self, ns):
        ns["mylist"] = [1, 2, 3]
        r = ns._redis()
        obj_key = make_object_key(ns.domain.name, ns.name, "mylist")
        meta = r.hgetall(obj_key)
        assert "binding_kind" not in meta

    def test_alias_in_ref_set(self, ns):
        ns["owner"] = [10, 20]
        ns["alias"] = ns["owner"]
        r = ns._redis()
        ref_key = make_ref_key(ns.domain.name, ns.name, "owner")
        assert r.smembers(ref_key) == {"alias"}


class TestAliasHandleProperties:
    """Verify alias handle identity and lifecycle resolution."""

    def test_alias_name_is_alias(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        obj = ns.get_object("alias")
        assert obj.name == "alias"

    def test_alias_canonical_name_is_owner(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        obj = ns.get_object("alias")
        assert obj.canonical_name == "owner"

    def test_alias_is_reference_true(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        obj = ns.get_object("alias")
        assert obj.is_reference is True

    def test_owner_is_reference_false(self, ns):
        ns["owner"] = [1, 2]
        obj = ns.get_object("owner")
        assert obj.is_reference is False

    def test_alias_uri_uses_alias_name(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        obj = ns.get_object("alias")
        assert "alias" in obj.uri
        assert "owner" not in obj.uri

    def test_alias_canonical_uri_uses_owner_name(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        obj = ns.get_object("alias")
        assert "owner" in obj.canonical_uri

    def test_alias_reads_same_data_as_owner(self, ns):
        ns["owner"] = [10, 20, 30]
        ns["alias"] = ns["owner"]
        assert ns["alias"].value == [10, 20, 30]

    def test_mutation_through_alias_visible_through_owner(self, ns):
        ns["owner"] = [10, 20]
        ns["alias"] = ns["owner"]
        ns["alias"].append(30)
        assert ns["owner"].value == [10, 20, 30]


class TestAliasLifecycleResolvesToCanonical:
    """Lifecycle properties on alias resolve to canonical owner."""

    def test_cache_mode_reads_from_canonical(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        assert ns.get_object("alias").cache_mode == ns.get_object("owner").cache_mode

    def test_cache_mode_set_through_alias_affects_canonical(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        alias_obj = ns.get_object("alias")
        alias_obj.cache_mode = "ttl"
        assert ns.get_object("owner").cache_mode == "ttl"

    def test_ttl_set_through_alias_affects_canonical(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        alias_obj = ns.get_object("alias")
        alias_obj.cache_mode = "ttl"
        alias_obj.ttl = 300
        assert ns.get_object("owner").ttl == 300

    def test_created_at_reads_from_canonical(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        assert ns.get_object("alias").created_at == ns.get_object("owner").created_at


class TestOwnerDeletionBlocking:
    """Owner deletion is blocked when referrers exist."""

    def test_delete_owner_with_refs_raises_error(self, ns):
        ns["owner"] = [1, 2, 3]
        ns["alias"] = ns["owner"]
        with pytest.raises(CanonicalObjectReferencedError) as exc_info:
            del ns["owner"]
        assert exc_info.value.canonical_name == "owner"
        assert "alias" in exc_info.value.referrers

    def test_delete_owner_without_refs_succeeds(self, ns):
        ns["owner"] = [1, 2, 3]
        del ns["owner"]
        assert "owner" not in ns

    def test_delete_alias_succeeds(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        del ns["alias"]
        assert "alias" not in ns
        assert "owner" in ns

    def test_delete_alias_removes_from_ref_set(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        del ns["alias"]
        r = ns._redis()
        ref_key = make_ref_key(ns.domain.name, ns.name, "owner")
        assert r.smembers(ref_key) == set()

    def test_delete_owner_after_all_aliases_removed(self, ns):
        ns["owner"] = [1, 2]
        ns["a1"] = ns["owner"]
        ns["a2"] = ns["owner"]
        del ns["a1"]
        del ns["a2"]
        del ns["owner"]
        assert "owner" not in ns


class TestOwnerOverwrite:
    """Owner containers can be overwritten when no refs exist."""

    def test_overwrite_owner_with_scalar(self, ns):
        ns["x"] = [1, 2, 3]
        ns["x"] = 42
        assert ns["x"] == 42

    def test_overwrite_owner_with_new_container(self, ns):
        ns["x"] = [1, 2, 3]
        ns["x"] = {"a": 1}
        assert ns["x"].value == {"a": 1}

    def test_overwrite_owner_with_refs_raises_error(self, ns):
        ns["x"] = [1, 2, 3]
        ns["y"] = ns["x"]
        with pytest.raises(CanonicalObjectReferencedError):
            ns["x"] = 42

    def test_overwrite_alias_with_scalar(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        ns["alias"] = 99
        assert ns["alias"] == 99
        assert ns["owner"].value == [1, 2]

    def test_overwrite_alias_with_new_container(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        ns["alias"] = {"new": True}
        assert ns["alias"].value == {"new": True}
        assert ns["owner"].value == [1, 2]


class TestSameRefNoOp:
    """Re-assigning the same container ref is a no-op."""

    def test_same_ref_assignment_is_noop(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        ns["alias"] = ns["owner"]
        r = ns._redis()
        ref_key = make_ref_key(ns.domain.name, ns.name, "owner")
        assert r.smembers(ref_key) == {"alias"}

    def test_self_assignment_of_owner_is_noop(self, ns):
        ns["x"] = [1, 2, 3]
        ns["x"] = ns["x"]
        assert ns["x"].value == [1, 2, 3]


class TestBindingResolution:
    """Test the _resolve_binding internal method."""

    def test_resolve_owner_hash(self, ns):
        ns["mylist"] = [1, 2]
        result = ns._resolve_binding("mylist")
        assert result is not None
        assert result["binding_kind"] == "owner"
        assert result["canonical_name"] == "mylist"
        assert result["binding_name"] == "mylist"

    def test_resolve_alias_string(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        result = ns._resolve_binding("alias")
        assert result is not None
        assert result["binding_kind"] == "reference"
        assert result["canonical_name"] == "owner"
        assert result["binding_name"] == "alias"

    def test_resolve_nonexistent_returns_none(self, ns):
        result = ns._resolve_binding("nosuchkey")
        assert result is None

    def test_resolve_scalar_owner(self, ns):
        ns["x"] = 42
        result = ns._resolve_binding("x")
        assert result is not None
        assert result["binding_kind"] == "owner"
        assert result["type"] == "scalar:int"

    def test_resolve_malformed_ref_returns_none(self, ns):
        r = ns._redis()
        alias_key = make_object_key(ns.domain.name, ns.name, "bad_alias")
        r.set(alias_key, "ref:gpdc:other_domain:other_ns:obj:something")
        result = ns._resolve_binding("bad_alias")
        assert result is None
        r.delete(alias_key)

    def test_resolve_alias_to_alias_returns_none(self, ns):
        r = ns._redis()
        ns["owner"] = [1, 2]
        ns["alias1"] = ns["owner"]
        alias1_key = make_object_key(ns.domain.name, ns.name, "alias1")
        alias2_key = make_object_key(ns.domain.name, ns.name, "alias2")
        r.set(alias2_key, f"ref:{alias1_key}")
        result = ns._resolve_binding("alias2")
        assert result is None
        r.delete(alias2_key)

    def test_resolve_string_binding_to_non_hash_returns_none(self, ns):
        r = ns._redis()
        owner_key = make_object_key(ns.domain.name, ns.name, "owner")
        alias_key = make_object_key(ns.domain.name, ns.name, "bad_alias")
        r.set(owner_key, "not_a_hash_value")
        r.set(alias_key, f"ref:{owner_key}")
        result = ns._resolve_binding("bad_alias")
        assert result is None
        r.delete(alias_key)
        r.delete(owner_key)


class TestContainerThroughAlias:
    """Container operations through alias handle work correctly."""

    def test_list_append_through_alias(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        ns["alias"].append(3)
        assert ns["owner"].value == [1, 2, 3]

    def test_dict_setitem_through_alias(self, ns):
        ns["owner"] = {"a": 1}
        ns["alias"] = ns["owner"]
        ns["alias"]["b"] = 2
        assert ns["owner"].value == {"a": 1, "b": 2}

    def test_set_add_through_alias(self, ns):
        ns["owner"] = {1, 2}
        ns["alias"] = ns["owner"]
        ns["alias"].add(3)
        assert 3 in ns["owner"]

    def test_alias_referrers_returns_canonical_refs(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        owner_obj = ns.get_object("owner")
        alias_obj = ns.get_object("alias")
        assert owner_obj.referrers() == {"alias"}
        assert alias_obj.referrers() == {"alias"}

    def test_alias_touch_updates_canonical_timestamp(self, ns):
        ns["owner"] = [1, 2]
        ns["alias"] = ns["owner"]
        owner_updated = ns.get_object("owner").updated_at
        ns["alias"].append(99)
        new_updated = ns.get_object("owner").updated_at
        assert new_updated >= owner_updated
