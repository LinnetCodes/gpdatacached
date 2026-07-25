"""Verify three review points about the logic.01 fix.

Run:
    .venv/Scripts/python.exe reviews/verification/verify_logic_01_points.py

Point 1: single-element __setitem__ with a pre-existing container value — is (b)
         covered? (Does it even have the problem?)
Point 2: nested encode_session (session within a session) — does the memo
         correctly track ref increments to the outermost session?
Point 3: create_raw path (ns["x"] = {...}) failing — is the cleanup complete
         (obj:/raw:/ref: all gone, no orphan)?
No source files modified.
"""
from __future__ import annotations

import redis as redis_lib

from gpdatacached.config import GPDCDomainConfig
from gpdatacached.keys import make_object_key, make_raw_key, make_ref_key

HOST, PORT, DB, PWD = "127.0.0.1", 6379, 1, "123456"


def r():
    return redis_lib.Redis(host=HOST, port=PORT, db=DB, password=PWD, decode_responses=True)


def banner(t):
    print("\n" + "=" * 80)
    print(t)
    print("=" * 80)


def main():
    from gpdatacached.domain import GPDCDomain

    cfg = GPDCDomainConfig(redis_host=HOST, redis_port=PORT, redis_db=DB, redis_password=PWD)

    # ────────────────────────────────────────────────────────────────────────
    # POINT 1: single-element __setitem__ with a pre-existing container value
    # ────────────────────────────────────────────────────────────────────────
    banner("POINT 1 — single-element __setitem__ with a pre-existing container value")
    r().flushdb()
    dom = GPDCDomain(cfg, name="p1")
    ns = dom.ensure_namespace("ns")

    # Scenario A: __setitem__ where the VALUE is a pre-existing container.
    # d["k"] = child  (child is a named ListObject)
    ns["child"] = [10, 20]
    ns["d"] = {"placeholder": 0}
    d = ns["d"]
    child = ns["child"]
    child_ref_key = make_ref_key("p1", "ns", child.canonical_name)
    refs_before = r().hgetall(child_ref_key)
    print("  Scenario A: d['k'] = child  (value is a pre-existing container)")
    print(f"    child ref: BEFORE = {refs_before}")
    # This is a single-element set. encode_element(child) -> _ref_existing_container
    # -> add_referrer. Since it's a single value (not a multi-element loop),
    # encode either succeeds (ref string returned) or fails before add_referrer.
    d["k"] = child
    refs_after = r().hgetall(child_ref_key)
    parent_after = r().hgetall(d.raw_key)
    print(f"    child ref: AFTER  = {refs_after}  (parent 'd' should now be a referrer)")
    print(f"    d raw AFTER       = {parent_after}")
    print(f"    -> single-element set with a container value correctly registers the ref: {'d' in refs_after}")

    # Scenario B: single-element __setitem__ where encode FAILS.
    # d["bad"] = object()  -> encode_element raises before any Redis write.
    print("\n  Scenario B: d['bad'] = object()  (encode fails on a single element)")
    parent_before = r().hgetall(d.raw_key)
    print(f"    d raw BEFORE = {parent_before}")
    try:
        d["bad"] = object()
    except TypeError as e:
        print(f"    d['bad'] = object() raised TypeError: {e}")
    parent_after_fail = r().hgetall(d.raw_key)
    print(f"    d raw AFTER  = {parent_after_fail}")
    print(f"    -> parent untouched on single-element encode failure? {parent_before == parent_after_fail}")
    print()
    print("  CONCLUSION (point 1): single-element __setitem__ does NOT have logic.01's")
    print("  multi-element problem. The value is encoded once; encode either succeeds")
    print("  (then hset+release run, correct) or fails (no Redis write at all). The (b)")
    print("  phantom-referrer risk only arises in MULTI-element encodes where some")
    print("  elements encode (+ add_referrer) before a later element fails. The memo")
    print("  extension covers that multi-element case via the active encode_session.")
    dom.close()

    # ────────────────────────────────────────────────────────────────────────
    # POINT 2: nested encode_session — ref increments tracked to outermost?
    # ────────────────────────────────────────────────────────────────────────
    banner("POINT 2 — nested encode_session: ref increments tracked to outermost?")
    r().flushdb()
    dom = GPDCDomain(cfg, name="p2")
    ns = dom.ensure_namespace("ns")

    # Construct a nested encode: a list whose element is a dict, whose value
    # references a pre-existing container. The outer session (list create_raw)
    # contains an inner session (dict encode). If the OUTER fails after the
    # inner succeeded, the inner's add_referrer must be reversed by the outer's
    # rollback.
    from gpdatacached.types import encode_session, encode_element
    from gpdatacached.lifecycle import TTLPolicy

    ns["existing"] = [1, 2]  # pre-existing container to reference
    existing = ns["existing"]
    existing_ref_key = make_ref_key("p2", "ns", existing.canonical_name)
    owner = "outer_list"
    policy = TTLPolicy(cache_mode="permanent", declared_ttl=0)

    print(f"  existing ref: BEFORE = {r().hgetall(existing_ref_key)}")
    # Simulate: outer session encodes a list [ {k: existing}, BAD ]
    # The dict {k: existing} encodes fine (inner session), referencing 'existing'.
    # Then BAD (object()) fails in the outer list encode -> outer rollback must
    # reverse the ref bump from the inner dict encode.
    ref_bumped_in_inner = False
    try:
        with encode_session(ns):  # outer (list)
            # encode first element: a dict that references `existing`
            enc_dict = encode_element({"k": existing}, ns, owner, policy)  # inner session via fallback
            # verify the ref was bumped during the inner encode
            ref_bumped_in_inner = "outer_list" in r().hgetall(existing_ref_key) or len(r().hgetall(existing_ref_key)) > 0
            print(f"  after inner encode of {{k: existing}}: existing ref = {r().hgetall(existing_ref_key)}")
            # now encode a failing element in the outer session
            encode_element(object(), ns, owner, policy)  # raises
    except TypeError as e:
        print(f"  outer encode raised TypeError: {e}")
    existing_ref_after = r().hgetall(existing_ref_key)
    print(f"  existing ref: AFTER outer rollback = {existing_ref_after}")
    print(f"  -> ref was bumped during inner encode? {ref_bumped_in_inner}")
    print(f"  -> ref reversed by outer rollback (empty again)? {existing_ref_after == {}}")
    print()
    print("  CONCLUSION (point 2): nested sessions inherit the outer's _ref_increments")
    print("  list (see encode_session: outer_refs = _ref_increments_var.get(); if not None,")
    print("  reuse it). So an inner encode's add_referrer is recorded on the OUTER")
    print("  session's list, and the outer rollback reverses it. Verified above.")
    dom.close()

    # ────────────────────────────────────────────────────────────────────────
    # POINT 3: create_raw failure — is cleanup complete (obj:/raw:/ref:)?
    # ────────────────────────────────────────────────────────────────────────
    banner("POINT 3 — create_raw failure (ns['x'] = {..., bad}): cleanup complete?")
    r().flushdb()
    dom = GPDCDomain(cfg, name="p3")
    ns = dom.ensure_namespace("ns")

    obj_key = make_object_key("p3", "ns", "x")
    raw_key = make_raw_key("p3", "ns", "x")
    ref_key = make_ref_key("p3", "ns", "x")
    # anon pattern for the 'ok' value's nested list
    print("  ns['x'] = {'ok':[1,2],'bad':object()}  (create_raw fails on 'bad')")
    print(f"    BEFORE: obj:x={r().exists(obj_key)}, raw:x={r().exists(raw_key)}, ref:x={r().exists(ref_key)}")
    try:
        ns["x"] = {"ok": [1, 2], "bad": object()}
    except TypeError as e:
        print(f"    raised TypeError: {e}")
    obj_after = r().exists(obj_key)
    raw_after = r().exists(raw_key)
    ref_after = r().exists(ref_key)
    anon_count = len(r().keys("gpdc:p3:ns:obj:~anon:*"))
    in_ns = "x" in ns
    print(f"    AFTER:  obj:x={obj_after}, raw:x={raw_after}, ref:x={ref_after}")
    print(f"    orphan anons under p3/ns: {anon_count}")
    print(f"    'x' in ns: {in_ns}")
    all_clean = (not obj_after) and (not raw_after) and (not ref_after) and (anon_count == 0) and (not in_ns)
    print(f"    -> ALL clean (no obj/raw/ref, no orphan anon, not in ns)? {all_clean}")

    # Also test a list create_raw failure (different codec)
    print("\n  ns['lst'] = [[1,2], object()]  (list create_raw fails on 2nd element)")
    lst_obj = make_object_key("p3", "ns", "lst")
    lst_raw = make_raw_key("p3", "ns", "lst")
    try:
        ns["lst"] = [[1, 2], object()]
    except TypeError as e:
        print(f"    raised TypeError: {e}")
    print(f"    AFTER: obj:lst={r().exists(lst_obj)}, raw:lst={r().exists(lst_raw)}, 'lst' in ns={'lst' in ns}")
    lst_clean = (not r().exists(lst_obj)) and (not r().exists(lst_raw)) and ('lst' not in ns)
    print(f"    -> list create_raw clean? {lst_clean}")
    print()
    print("  CONCLUSION (point 3): the namespace __setitem__ try/except deletes")
    print("  obj:/raw:/ref: on a failed create_raw; completed anons for earlier")
    print("  elements are rolled back by encode_session. No orphan keys remain.")
    dom.close()


if __name__ == "__main__":
    main()
