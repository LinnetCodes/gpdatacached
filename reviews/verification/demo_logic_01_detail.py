"""Detailed walkthrough of Finding logic.01: a failed multi-element encode leaves
Redis in a state no successful operation could produce.

Run:
    .venv/Scripts/python.exe reviews/verification/demo_logic_01_detail.py

Shows the THREE classes of un-rolled-back side effects:
  [A] DictObject.update — partial parent write (a dangling ref: pointer).
  [B] ListObject.extend — phantom referrer on a pre-existing child (blocks reclamation).
  [C] DictObject.update overwrite — old element ref released before failure (data loss).

And contrasts with what encode_session DOES roll back (anonymous objects only).
No source files modified.
"""
from __future__ import annotations

import redis as redis_lib

from gpdatacached.config import GPDCDomainConfig
from gpdatacached.keys import make_object_key, make_raw_key, make_ref_key, parse_ref_encoded

HOST, PORT, DB, PWD = "127.0.0.1", 6379, 1, "123456"


def banner(t):
    print("\n" + "=" * 80)
    print(t)
    print("=" * 80)


def show(label, value):
    print(f"    {label}: {value!r}")


def main():
    banner("logic.01 — failed multi-element encode leaves partial writes / phantom refs")
    from gpdatacached.domain import GPDCDomain

    cfg = GPDCDomainConfig(redis_host=HOST, redis_port=PORT, redis_db=DB, redis_password=PWD)
    r = redis_lib.Redis(host=HOST, port=PORT, db=DB, password=PWD, decode_responses=True)

    # ── [A] DictObject.update: partial parent write (dangling ref pointer) ────
    print("\n[A] DictObject.update({'ok':[1,2],'bad':object()}) — partial parent write")
    r.flushdb()
    dom = GPDCDomain(cfg, name="logic01a")
    ns = dom.ensure_namespace("ns")
    ns["d"] = {"placeholder": 0}  # a dict that already exists
    d = ns["d"]
    before = r.hgetall(d.raw_key)
    show("parent dict BEFORE", before)
    try:
        d.update({"ok": [1, 2], "bad": object()})  # 'ok' encodes (creates an anon list), 'bad' fails
    except TypeError as e:
        print(f"    update() raised TypeError as expected: {e}")
    after = r.hgetall(d.raw_key)
    show("parent dict AFTER  ", after)
    ok_val = after.get("str:ok")
    print(f"    -> 'str:ok' present after failure? {ok_val is not None}")
    if ok_val and ok_val.startswith("ref:"):
        anon_obj_key = ok_val[len("ref:"):]
        anon_exists = r.exists(anon_obj_key)
        print(f"    -> the anon it points to (obj:{anon_obj_key}) exists? {anon_exists}")
        print(f"    -> DANGLING ref: parent points at a DELETED anon (rollback killed the anon")
        print(f"       but left the parent's pointer to it). .value/.get on 'ok' will break.")
        # prove read-back breaks:
        try:
            _ = d["ok"]
            print(f"    -> d['ok'] read back OK (unexpected)")
        except Exception as e:
            print(f"    -> d['ok'] read-back RAISES {type(e).__name__}: {str(e)[:80]}")
    dom.close()

    # ── [B] ListObject.extend: phantom referrer on a pre-existing child ───────
    print("\n[B] list.extend([existing_child, object()]) — phantom referrer")
    r.flushdb()
    dom = GPDCDomain(cfg, name="logic01b")
    ns = dom.ensure_namespace("ns")
    ns["child"] = [10, 20]            # a pre-existing named container
    ns["parent"] = []                  # the parent list
    parent = ns["parent"]
    child = ns["child"]
    child_canonical = child.canonical_name
    child_ref_key = make_ref_key("logic01b", "ns", child_canonical)
    refs_before = r.hgetall(child_ref_key)
    parent_raw_before = r.lrange(parent.raw_key, 0, -1)
    show("child ref: BEFORE extend", refs_before)
    show("parent raw BEFORE extend", parent_raw_before)
    try:
        parent.extend([child, object()])  # child encodes (+ add_referrer), then 'object()' fails
    except TypeError as e:
        print(f"    extend() raised TypeError as expected: {e}")
    refs_after = r.hgetall(child_ref_key)
    parent_raw_after = r.lrange(parent.raw_key, 0, -1)
    show("child ref: AFTER  extend", refs_after)
    show("parent raw AFTER  extend", parent_raw_after)
    phantom = (parent_raw_after == []) and ("parent" in refs_after)
    print(f"    -> PHANTOM referrer: 'parent' in child's ref: but parent list is empty? {phantom}")
    print(f"    -> Consequence: child now believes 'parent' references it, so child won't be")
    print(f"       cascade-deleted when its real referrers go away. A leak.")
    dom.close()

    # ── [C] DictObject.update overwrite: old ref released before failure ──────
    print("\n[C] Dict.update overwrites a slot holding an anon, then fails — old anon released early")
    r.flushdb()
    dom = GPDCDomain(cfg, name="logic01c")
    ns = dom.ensure_namespace("ns")
    ns["d"] = {"k": [1, 2, 3]}   # 'k' holds an anonymous list child
    d = ns["d"]
    # find the anon under 'k'
    raw = r.hgetall(d.raw_key)
    k_val = raw.get("str:k")
    anon_name = parse_ref_encoded(k_val) if k_val and k_val.startswith("ref:") else None
    anon_existed = anon_name and r.exists(make_object_key("logic01c", "ns", anon_name))
    show("anon under 'k' EXISTS before update?", bool(anon_existed))
    before_k = r.hget(d.raw_key, "str:k")
    try:
        # overwrite 'k' with a valid value FIRST, then fail on 'bad'.
        # update() does per-pair: encode 'k'->new (releases old anon ref), then 'bad' fails.
        d.update({"k": 999, "bad": object()})
    except TypeError as e:
        print(f"    update() raised TypeError as expected: {e}")
    after_k = r.hget(d.raw_key, "str:k")
    anon_still = r.exists(make_object_key("logic01c", "ns", anon_name)) if anon_name else None
    show("'str:k' BEFORE", before_k)
    show("'str:k' AFTER ", after_k)
    show("anon under old 'k' still EXISTS?", anon_still)
    # Did 'k' get overwritten even though the overall update failed?
    k_changed = (before_k != after_k)
    print(f"    -> 'k' slot was MODIFIED despite the overall update failure? {k_changed}")
    print(f"    -> The old anon under 'k' had its ref released during the failed encode, so it can")
    print(f"       now be orphaned/lost — a successful operation would have left Redis untouched.")
    print(f"       (Whether the anon is gone depends on whether its refcount hit 0; the point is")
    print(f"       the release happened at all during a failing operation.)")
    dom.close()

    # ── [D] What encode_session DOES roll back (for contrast) ─────────────────
    print("\n[D] For contrast — what encode_session DOES roll back: anonymous objects only")
    print("    _rollback_encode_memo (types.py:165-177) deletes ONLY the obj:/raw:/ref: of")
    print("    anonymous objects created during the session. It does NOT reverse:")
    print("      - parent raw writes (the dangling ref: pointer in [A], the 'k' overwrite in [C])")
    print("      - add_referrer on PRE-EXISTING containers (the phantom referrer in [B])")
    print("      - _release_element_ref of old values (the early release in [C])")
    print("    So a failing multi-element encode can leave Redis in a state no successful op produces.")

    # ── VERDICT ───────────────────────────────────────────────────────────────
    banner("SUMMARY")
    print("  - [A] Dict.update partial write: parent keeps a 'str:ok' -> ref:<deleted anon>; read breaks.")
    print("  - [B] list.extend phantom ref: 'parent' counted as a referrer of 'child' but parent is empty.")
    print("  - [C] Dict.update overwrite: old element ref released mid-failure (data can be orphaned).")
    print("  - encode_session rolls back ONLY anonymous-object creation; the above three are un-rolled.")
    print("  - Violates the documented 'leaves Redis untouched on failure' guarantee.")
    print("  - Fix options: stage all encoded values + ref deltas in memory, commit atomically after")
    print("    full encode succeeds; OR a Redis undo journal; OR encode into temp keys + atomic swap.")


if __name__ == "__main__":
    main()
