"""Tests for encode memo: cycle detection (H2) and aliasing preservation (H3)."""

from __future__ import annotations

import pytest

from gpdatacached import CyclicReferenceError
from gpdatacached.exceptions import GPDCError
from gpdatacached.keys import make_object_key, make_raw_key, make_ref_key


def _ref_count(ns, anon_name):
    """Read the total reference count for an anon object from Redis."""
    r = ns._redis()
    ref_key = make_ref_key("testdomain", "testns", anon_name)
    return sum(int(v) for v in r.hvals(ref_key))


class TestCyclicReferenceError:
    def test_is_gpdc_error_subclass(self):
        assert issubclass(CyclicReferenceError, GPDCError)

    def test_message_contains_type_name(self):
        err = CyclicReferenceError("dict")
        assert "dict" in str(err)
        assert err.type_name == "dict"

    def test_exported_from_package(self):
        from gpdatacached import CyclicReferenceError as ImportedException

        assert ImportedException is CyclicReferenceError


class TestCycleDetection:
    def test_direct_dict_self_cycle(self, ns):
        d = {}
        d["self"] = d
        with pytest.raises(CyclicReferenceError, match="dict"):
            ns["x"] = d

    def test_indirect_cycle(self, ns):
        a = []
        b = [a]
        a.append(b)
        with pytest.raises(CyclicReferenceError, match="list"):
            ns["x"] = a

    def test_list_self_cycle(self, ns):
        lst = []
        lst.append(lst)
        with pytest.raises(CyclicReferenceError, match="list"):
            ns["x"] = lst

    def test_cycle_in_append_path(self, ns):
        ns["lst"] = [1, 2]
        lst = ns["lst"]
        d = {}
        d["self"] = d
        with pytest.raises(CyclicReferenceError, match="dict"):
            lst.append(d)

    def test_cycle_error_is_gpdc_error(self, ns):
        from gpdatacached.exceptions import GPDCError

        lst = []
        lst.append(lst)
        with pytest.raises(GPDCError):
            ns["x"] = lst

    def test_no_orphaned_anon_after_cycle_error(self, ns, raw_redis):
        d = {}
        d["self"] = d
        with pytest.raises(CyclicReferenceError):
            ns["x"] = d
        anon_keys = raw_redis.keys("gpdc:testdomain:testns:obj:~anon:*")
        assert anon_keys == []

    def test_no_orphaned_anon_with_preceding_sibling_on_cycle_error(self, ns, raw_redis):
        d = {"a": [1, 2]}
        d["self"] = d
        with pytest.raises(CyclicReferenceError):
            ns["x"] = d
        anon_keys = raw_redis.keys("gpdc:testdomain:testns:obj:~anon:*")
        assert anon_keys == []

    def test_non_cyclic_deep_nesting_still_works(self, ns):
        ns["x"] = {"a": {"b": {"c": {"d": [1, 2, 3]}}}}
        d = ns["x"]
        assert d["a"]["b"]["c"]["d"][0] == 1


class TestAliasingSingleCall:
    def test_nested_alias_in_appended_dict(self, ns):
        """Single append with internal alias — fallback session handles it."""
        ns["lst"] = []
        lst = ns["lst"]
        shared = [1, 2, 3]
        lst.append({"a": shared, "b": shared})
        item = lst[0]
        assert item["a"].name == item["b"].name  # same canonical anon
        assert _ref_count(ns, item["a"].name) == 2

    def test_alias_mutation_visible_through_both_refs(self, ns):
        ns["lst"] = []
        lst = ns["lst"]
        shared = [1, 2]
        lst.append({"a": shared, "b": shared})
        item = lst[0]
        item["a"].append(99)
        assert list(item["b"]) == [1, 2, 99]

    def test_alias_gc_delete_one_slot_survives(self, ns, raw_redis):
        ns["lst"] = []
        lst = ns["lst"]
        shared = [1, 2]
        lst.append({"a": shared, "b": shared})
        item = lst[0]
        anon_name = item["a"].name

        del item["a"]

        assert _ref_count(ns, anon_name) == 1
        assert raw_redis.exists(make_object_key("testdomain", "testns", anon_name))

    def test_alias_gc_delete_both_slots_cleans_up(self, ns, raw_redis):
        ns["lst"] = []
        lst = ns["lst"]
        shared = [1, 2]
        lst.append({"a": shared, "b": shared})
        item = lst[0]
        anon_name = item["a"].name

        del item["a"]
        del item["b"]

        assert not raw_redis.exists(make_object_key("testdomain", "testns", anon_name))
        assert not raw_redis.exists(make_raw_key("testdomain", "testns", anon_name))

    def test_scalar_aliasing_has_no_special_behavior(self, ns):
        """Scalars are immutable — both occurrences are plain int values."""
        x = 42
        ns["k"] = {"a": x, "b": x}
        d = ns["k"]
        assert d["a"] == 42
        assert d["b"] == 42


class TestSiblingAliasing:
    def test_dict_sibling_alias_via_setitem(self, ns):
        shared = [1, 2, 3]
        ns["x"] = {"a": shared, "b": shared}
        d = ns["x"]
        assert d["a"].name == d["b"].name
        assert _ref_count(ns, d["a"].name) == 2

    def test_list_sibling_alias_via_extend(self, ns):
        ns["lst"] = []
        lst = ns["lst"]
        shared = [1, 2]
        lst.extend([shared, shared])
        assert lst[0].name == lst[1].name
        assert _ref_count(ns, lst[0].name) == 2

    def test_dict_sibling_alias_via_update(self, ns):
        ns["d"] = {}
        d = ns["d"]
        shared = [1, 2]
        d.update({"a": shared, "b": shared})
        assert d["a"].name == d["b"].name
        assert _ref_count(ns, d["a"].name) == 2

    def test_sibling_alias_mutation_visible(self, ns):
        shared = [1, 2, 3]
        ns["x"] = {"a": shared, "b": shared}
        d = ns["x"]
        d["a"].append(99)
        assert list(d["b"]) == [1, 2, 3, 99]

    def test_sibling_alias_gc_whole_parent(self, ns, raw_redis):
        shared = [1, 2, 3]
        ns["x"] = {"a": shared, "b": shared}
        d = ns["x"]
        anon_name = d["a"].name

        del ns["x"]

        assert not raw_redis.exists(make_object_key("testdomain", "testns", anon_name))
        assert not raw_redis.exists(make_raw_key("testdomain", "testns", anon_name))

    def test_non_aliased_siblings_create_distinct_anons(self, ns):
        ns["x"] = {"a": [1, 2], "b": [1, 2]}
        d = ns["x"]
        assert d["a"].name != d["b"].name


class TestFailedEncodeLeavesRedisUntouchedLogic01:
    """CQR-2026-07-24 logic.01: a failed multi-element encode must leave Redis in
    the same state as before the call (no partial parent write, no phantom
    referrer, no early release of old refs)."""

    def test_dict_update_partial_write_reverted(self, ns, raw_redis):
        # [A] update() writes pairs one at a time; a later failing pair must not
        # leave the already-encoded 'ok' slot in the parent hash.
        ns["d"] = {"placeholder": 0}
        d = ns["d"]
        before = raw_redis.hgetall(d.raw_key)
        with pytest.raises(TypeError):
            d.update({"ok": [1, 2], "bad": object()})
        assert raw_redis.hgetall(d.raw_key) == before
        # And no orphaned anon created for 'ok':
        assert not raw_redis.keys("gpdc:testdomain:testns:obj:~anon:*")

    def test_dict_update_old_ref_not_released_early(self, ns, raw_redis):
        # [C] update() overwriting a slot holding an anon, then failing on a
        # later pair, must NOT release/delete the old anon.
        ns["d"] = {"k": [1, 2, 3], "keep": 1}
        d = ns["d"]
        raw = raw_redis.hgetall(d.raw_key)
        old_k = raw["str:k"]
        old_anon_name = old_k.split(":obj:")[1]
        before = raw_redis.hgetall(d.raw_key)
        with pytest.raises(TypeError):
            d.update({"k": 999, "bad": object()})
        # Parent hash unchanged, old anon still present and still referenced.
        assert raw_redis.hgetall(d.raw_key) == before
        assert raw_redis.exists(make_object_key("testdomain", "testns", old_anon_name))

    def test_list_extend_phantom_referrer_reverted(self, ns, raw_redis):
        # [B] extend() referencing a pre-existing child, then failing, must not
        # leave the parent as a phantom referrer of the child.
        ns["child"] = [10, 20]
        ns["parent"] = []
        parent = ns["parent"]
        child = ns["child"]
        child_ref_key = make_ref_key("testdomain", "testns", child.canonical_name)
        assert raw_redis.hgetall(child_ref_key) == {}  # no referrers before
        with pytest.raises(TypeError):
            parent.extend([child, object()])
        # No phantom referrer left; child's ref: empty again.
        assert raw_redis.hgetall(child_ref_key) == {}
        assert raw_redis.lrange(parent.raw_key, 0, -1) == []

    def test_namespace_setitem_failed_encode_no_orphan_meta(self, ns, raw_redis):
        # First-store path: ns["x"] = {..., "bad": object()} fails; the obj:x
        # meta written before the encode session must be cleaned up.
        obj_key = make_object_key("testdomain", "testns", "x")
        with pytest.raises(TypeError):
            ns["x"] = {"ok": [1, 2], "bad": object()}
        assert not raw_redis.exists(obj_key)
        assert not raw_redis.exists(make_raw_key("testdomain", "testns", "x"))
        assert "x" not in ns

    def test_dict_update_succeeds_normally(self, ns):
        # Sanity: a normal (non-failing) update still works after the staging refactor.
        ns["d"] = {"a": 1}
        ns["d"].update({"a": 2, "b": 3, "c": [1, 2]})
        d = ns["d"]
        assert d["a"] == 2
        assert d["b"] == 3
        assert list(d["c"]) == [1, 2]

