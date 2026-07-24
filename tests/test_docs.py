# tests/test_docs.py
"""Regression tests that the documented `take_ownership()` examples actually
produce the owner values the docs promise.

Background (CQR-2026-07-24 Finding gap.01): the docs previously described the
*opposite* semantics — they said a named reference "promotes itself" and that
"anonymous objects cannot" use take_ownership. Both claims were backwards:
`take_ownership` operates on the *referenced object* (making it self-owned),
and anonymous objects are its *primary* use case. These tests pin the corrected
behaviour by executing the doc example verbatim and asserting the documented
post-condition, so the docs cannot silently drift back.

These are real Redis integration tests (same fixtures as the rest of the suite).
"""
from __future__ import annotations

from gpdatacached.keys import make_object_key
from gpdatacached.meta import ObjectMeta


class TestTakeOwnershipDocExample:
    """Mirror of the lifecycle.md §7 / overview.md take_ownership example."""

    def test_named_reference_to_anonymous_object_becomes_self_owned(self, ns, clean_redis):
        # Verbatim from docs/en/lifecycle.md §7 and docs/en/overview.md §6:
        #   ns["parent"] = {"inner": [1, 2, 3]}
        #   inner = ns.get_object("parent")["inner"]
        #   ns["named"] = inner
        #   obj = ns.get_object("named")
        #   obj.take_ownership()   # the ~anon:… now owns itself; "parent" unaffected
        ns["parent"] = {"inner": [1, 2, 3]}
        inner = ns.get_object("parent")["inner"]
        ns["named"] = inner
        obj = ns.get_object("named")

        anon_canonical = obj.canonical_name
        assert anon_canonical.startswith("~anon:"), "expected an anonymous referenced object"

        # BEFORE: the anon is owned by "parent" (attached to the parent container).
        r = clean_redis
        meta_before = ObjectMeta.load(r, make_object_key(ns.domain.name, ns.name, anon_canonical))
        assert meta_before.owner == "parent"

        obj.take_ownership()

        # AFTER (the documented post-condition): owner == the anon's own canonical name.
        meta_after = ObjectMeta.load(r, make_object_key(ns.domain.name, ns.name, anon_canonical))
        assert meta_after.owner == anon_canonical

    def test_promoted_anonymous_object_has_independent_lifecycle(self, ns, clean_redis):
        # The doc comment promises: setting a TTL on the promoted anon does NOT
        # affect "parent" — the anon now has its own lifecycle.
        ns["parent"] = {"inner": [1, 2, 3]}
        ns["named"] = ns.get_object("parent")["inner"]
        obj = ns.get_object("named")
        obj.take_ownership()
        obj.update_cache_mode_and_ttl("ttl", 60)

        r = clean_redis
        anon_key = make_object_key(ns.domain.name, ns.name, obj.canonical_name)
        parent_key = make_object_key(ns.domain.name, ns.name, "parent")

        anon_meta = ObjectMeta.load(r, anon_key)
        parent_meta = ObjectMeta.load(r, parent_key)
        assert anon_meta.cache_mode == "ttl" and anon_meta.ttl == 60
        # parent is untouched — it keeps its own (default permanent) policy.
        assert parent_meta.cache_mode == "permanent"
        assert r.ttl(parent_key) == -1  # persistent

    def test_reference_to_named_object_is_a_correct_noop(self, ns, clean_redis):
        # The doc states: on a reference to a named (already self-owned) object,
        # take_ownership() is a correct no-op. This guards that documented
        # behaviour (a named object is its own owner; the call changes nothing).
        ns["original"] = [1, 2, 3]
        ns["alias"] = ns["original"]
        alias_obj = ns.get_object("alias")
        assert alias_obj.is_reference
        assert alias_obj.canonical_name == "original"

        r = clean_redis
        owner_before = ObjectMeta.load(r, make_object_key(ns.domain.name, ns.name, "original")).owner

        alias_obj.take_ownership()  # no error, no change

        owner_after = ObjectMeta.load(r, make_object_key(ns.domain.name, ns.name, "original")).owner
        assert owner_before == "original"
        assert owner_after == "original"

    def test_take_ownership_requires_named_reference_handle(self, domain, ns):
        # The doc states the call must be made via a named reference handle;
        # calling on a plain (non-reference) object raises TypeError.
        import pytest

        ns["obj"] = 42
        obj = ns.get_object("obj")
        with pytest.raises(TypeError, match="named reference"):
            obj.take_ownership()
