"""Verify gap.01 against the DESIGNER'S stated intent.

Designer's intent (per maintainer):
  - take_ownership targets ANONYMOUS objects (~anon:...), NOT named references.
  - An anonymous object is created as part of a parent container; its lifecycle
    is attached to (owned by) its parent container (owner = parent_name).
  - When a NAMED reference points at that anonymous object, the user has begun
    to care about it. The user may then call take_ownership on that anonymous
    object to make ITSELF the lifecycle owner (owner = ~anon:..., i.e. the anon
    becomes self-owned), so it can carry an independent TTL/policy.
  - Named objects are always owner=self, so take_ownership on a named reference
    is meaningless (and should be a no-op or rejected).

The CQR report (and my first demo) used two NAMED objects
(`ns['alias'] = ns['original']`) — that is NOT the intended target. This demo
exercises the intended path: a named reference to an ANONYMOUS nested object.

Run:
    .venv/Scripts/python.exe reviews/verification/demo_gap_01_intent.py
No source files modified.
"""
from __future__ import annotations

import redis as redis_lib

from gpdatacached.config import GPDCDomainConfig

HOST, PORT, DB, PWD = "127.0.0.1", 6379, 1, "123456"
DOMAIN, NS = "gap01intent", "ns"


def r():
    return redis_lib.Redis(host=HOST, port=PORT, db=DB, password=PWD, decode_responses=True)


def banner(t):
    print("\n" + "=" * 80)
    print(t)
    print("=" * 80)


def show(label, value):
    print(f"    {label}: {value!r}")


def anon_state(dom, ns, step, anon_name):
    from gpdatacached.keys import make_object_key, make_ref_key

    rr = r()
    anon_key = make_object_key(DOMAIN, NS, anon_name)
    meta = rr.hgetall(anon_key)
    print(f"  {step}")
    show("anon canonical_name", anon_name)
    show("anon meta.owner", meta.get("owner"))
    show("anon meta.cache_mode", meta.get("cache_mode"))
    show("anon meta.ttl", meta.get("ttl"))
    show("anon ref: (referrers -> counts)", rr.hgetall(make_ref_key(DOMAIN, NS, anon_name)))
    show("anon Redis TTL (obj:~anon)", rr.ttl(anon_key))


def _obj_key(name):
    from gpdatacached.keys import make_object_key

    return make_object_key(DOMAIN, NS, name)


def main():
    banner("gap.01 — take_ownership on an ANONYMOUS nested object (the INTENDED path)")
    r().flushdb()
    from gpdatacached.domain import GPDCDomain

    cfg = GPDCDomainConfig(redis_host=HOST, redis_port=PORT, redis_db=DB, redis_password=PWD)
    dom = GPDCDomain(cfg, name=DOMAIN)
    ns = dom.ensure_namespace(NS)

    # ── STEP 1: parent dict owns an anonymous nested list ───────────────────
    print("\n[1] ns['parent'] = {'inner': [1, 2, 3]}   # inner becomes ~anon owned by 'parent'")
    ns["parent"] = {"inner": [1, 2, 3]}
    inner = ns.get_object("parent")["inner"]  # the anonymous list object
    anon_name = inner.canonical_name
    anon_state(dom, ns, "after step 1", anon_name)
    print(f"  >>> inner.is_reference = {inner.is_reference}; owner='parent' (attached to parent)")

    # ── STEP 2: create a NAMED reference pointing at the anonymous object ────
    print("\n[2] ns['named'] = inner   # a named reference to the anonymous object")
    ns["named"] = inner
    named_obj = ns.get_object("named")
    anon_state(dom, ns, "after step 2  (ref count goes up)", anon_name)
    show("named_obj.name", named_obj.name)
    show("named_obj.canonical_name", named_obj.canonical_name)
    show("named_obj.is_reference", named_obj.is_reference)
    print("  >>> named_obj is a reference; canonical_name is the ~anon:... it points at.")

    # ── STEP 3: call take_ownership on the ANONYMOUS object via the reference ─
    print("\n[3] named_obj.take_ownership()   # intent: the ~anon becomes self-owned")
    print("    >>> object.py:193 does meta.with_updates(owner=self.canonical_name)")
    print("    >>> for the reference, self.canonical_name == ~anon:... (the target)")
    named_obj.take_ownership()
    anon_state(dom, ns, "after step 3", anon_name)
    became_self_owned = r().hget(_obj_key(anon_name), "owner") == anon_name
    print(f"\n  >>> DID the anonymous object become self-owned (owner == ~anon:...)? {became_self_owned}")

    # ── STEP 4: now set an independent TTL on the (now self-owned) anon ──────
    print("\n[4] Now set lifecycle on the anonymous object — should be independent of parent")
    print("    named_obj.update_cache_mode_and_ttl('ttl', 60)")
    named_obj.update_cache_mode_and_ttl("ttl", 60)
    anon_state(dom, ns, "after step 4", anon_name)
    # check the parent is UNAFFECTED
    parent_meta = r().hgetall(_obj_key("parent"))
    print(f"    parent meta.cache_mode: {parent_meta.get('cache_mode')!r}  (should stay 'permanent')")
    print(f"    parent Redis TTL: {r().ttl(_obj_key('parent'))}  (-1 = persistent)")

    # ── VERDICT ──────────────────────────────────────────────────────────────
    banner("VERDICT")
    if became_self_owned:
        print("  On the INTENDED path (named ref -> anonymous object), take_ownership WORKS:")
        print("  - The anonymous object's owner becomes its own canonical name.")
        print("  - It can then carry an independent lifecycle (TTL set on it does NOT")
        print("    touch the parent).")
        print()
        print("  => The CQR report's 'gap.01' finding is a FALSE POSITIVE for this path.")
        print("  => The report tested the WRONG scenario (two named objects).")
        print()
        print("  Open question for the maintainer: what SHOULD happen when take_ownership")
        print("  is called on a named reference that points at a NAMED (non-anon) object?")
        print("  Today it's a silent no-op. The report flags that; the maintainer's intent")
        print("  is that named objects 'can never be take_ownership-ed' — so the question is")
        print("  whether silent no-op should become an explicit raise (TypeError) for clarity.")
    else:
        print("  Even on the intended path, take_ownership did NOT make the anon self-owned.")
        print("  => The finding still holds.")

    dom.close()


if __name__ == "__main__":
    main()
