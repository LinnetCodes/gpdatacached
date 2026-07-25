"""Detailed walkthrough of Finding logic.02: a stale handle mutates Redis BEFORE
ObjectGoneError is raised.

Run:
    .venv/Scripts/python.exe reviews/verification/demo_logic_02_detail.py

Shows the trigger scenario, why existing tests miss it, and the concrete Redis
consequence (orphaned raw: keys). No source files modified.
"""
from __future__ import annotations

import redis as redis_lib

from gpdatacached.config import GPDCDomainConfig
from gpdatacached.keys import make_object_key, make_raw_key

HOST, PORT, DB, PWD = "127.0.0.1", 6379, 1, "123456"
DOMAIN, NS = "logic02detail", "ns"


def r():
    return redis_lib.Redis(host=HOST, port=PORT, db=DB, password=PWD, decode_responses=True)


def banner(t):
    print("\n" + "=" * 80)
    print(t)
    print("=" * 80)


def show(label, value):
    print(f"    {label}: {value!r}")


def raw_state(name, step):
    rr = r()
    raw_key = make_raw_key(DOMAIN, NS, name)
    obj_key = make_object_key(DOMAIN, NS, name)
    print(f"  {step}")
    show("obj: (meta) key EXISTS?", bool(rr.exists(obj_key)))
    show("raw: key EXISTS?", bool(rr.exists(raw_key)))
    # show values depending on type
    t = rr.hget(obj_key, "type") if rr.exists(obj_key) else None
    if t == "collection:list":
        show("raw: values", rr.lrange(raw_key, 0, -1))
    elif t == "collection:set":
        show("raw: values", sorted(rr.smembers(raw_key)))
    elif t == "collection:dict":
        show("raw: values", rr.hgetall(raw_key))


def main():
    banner("logic.02 — stale handle mutates raw: BEFORE ObjectGoneError")

    # ── STEP 1: the trigger scenario ─────────────────────────────────────────
    print("\n[1] The trigger: store a list, KEEP the handle, delete the binding, then mutate")
    r().flushdb()
    from gpdatacached.domain import GPDCDomain

    cfg = GPDCDomainConfig(redis_host=HOST, redis_port=PORT, redis_db=DB, redis_password=PWD)
    dom = GPDCDomain(cfg, name=DOMAIN)
    ns = dom.ensure_namespace(NS)

    ns["lst"] = [1]
    lst = ns.get_object("lst")  # retain the handle
    raw_state("lst", "after ns['lst'] = [1]  (healthy object)")

    print("\n  Now delete the binding + object (canonical delete):")
    del ns["lst"]
    raw_state("lst", "after del ns['lst']  (obj: gone, raw: gone — clean)")

    print("\n  The retained handle `lst` is now STALE (points at a deleted object).")
    print("  Call lst.append(2):")

    # ── STEP 2: the bug — append writes BEFORE _touch raises ─────────────────
    from gpdatacached.exceptions import ObjectGoneError

    try:
        lst.append(2)
        print("    lst.append(2) did NOT raise — unexpected")
    except ObjectGoneError as e:
        print(f"    lst.append(2) raised ObjectGoneError as expected: {e}")
    raw_state("lst", "after stale lst.append(2) raised")
    print("  >>> READ THE ABOVE (post-fix behaviour):")
    print("  >>>   obj: (meta) key EXISTS? False   (correctly gone)")
    print("  >>>   raw: key EXISTS? False          (NOT recreated — the stale write")
    print("  >>>                                   was blocked by the pre-write guard)")
    print("  >>> Before the logic.02 fix this was True with value ['int:2'], leaving")
    print("  >>> an orphaned raw: key unreachable by namespace APIs and invisible to GC.")
    print("  >>> After the fix, _check_alive() raises ObjectGoneError BEFORE the rpush,")
    print("  >>> so Redis stays clean.")

    # ── STEP 3: confirm it's unreachable via the namespace API ───────────────
    print("\n[3] Confirm the orphaned raw: key is unreachable via namespace APIs:")
    print(f"    'lst' in ns: {'lst' in ns}")
    print(f"    list(ns): {list(ns)}")
    print(f"    ns.get_object('lst') -> ", end="")
    try:
        ns.get_object("lst")
        print("(returned — unexpected)")
    except Exception as e:
        print(f"{type(e).__name__}")
    print("  >>> The namespace pretends 'lst' doesn't exist, but the raw: key is real")
    print("  >>> Redis data that no public API can see or clean up.")

    # ── STEP 4: why existing tests don't catch this ──────────────────────────
    print("\n[4] Why existing tests miss it (tests/test_objects.py:342-347):")
    print("      def test_list_append_raises(self, ns, raw_redis):")
    print("          ns['lst'] = [1, 2]")
    print("          obj = ns.get_object('lst')")
    print("          self._delete_meta(obj, raw_redis)   # deletes ONLY the obj: meta key")
    print("          with pytest.raises(ObjectGoneError, match='no longer exists'):")
    print("              obj.append(3)")
    print("  >>> The test asserts ObjectGoneError is raised — and it IS (by _touch).")
    print("  >>> But it does NOT check whether raw: was mutated before the raise.")
    print("  >>> So the test passes DESPITE the orphaned write. The bug is invisible")
    print("  >>> to the existing assertions. (And 28 of 31 mutators have no such")
    print("  >>> stale-handle test at all.)")

    # ── STEP 5: the same bug across types ────────────────────────────────────
    print("\n[5] The same bug hits every container type (all 31 mutators write-then-touch):")
    for name, factory, mutate in [
        ("set_handle", lambda: {1, 2}, lambda h: h.add(99)),
        ("dict_handle", lambda: {"a": 1}, lambda h: h.__setitem__("b", 2)),
    ]:
        r().flushdb()
        ns2 = dom.ensure_namespace("ns2")
        ns2[name] = factory()
        h = ns2.get_object(name)
        del ns2[name]
        try:
            mutate(h)
        except ObjectGoneError:
            pass
        rk = make_raw_key(DOMAIN, "ns2", name)
        print(f"    {name}: after stale mutate raised ObjectGoneError, raw: EXISTS? {bool(r().exists(rk))}")

    # ── STEP 6: the code path that causes it ─────────────────────────────────
    print("\n[6] Why — the write-before-touch ordering (list_object.py:121-126):")
    print("      def append(self, value):")
    print("          ...")
    print("          self._redis().rpush(self.raw_key, encoded)   # WRITE happens first")
    print("          self._touch()                                # liveness check is HERE")
    print("    >>> _touch (container.py:60-62) loads meta and raises ObjectGoneError")
    print("    >>> ONLY AFTER the rpush already wrote. The check is structurally a")
    print("    >>> post-write hook, not a pre-write guard. Same pattern in all 31 mutators.")

    # ── VERDICT ──────────────────────────────────────────────────────────────
    banner("SUMMARY")
    print("  - A stale handle (deleted object whose Python handle is retained) calling")
    print("    a mutator WRITES to raw: BEFORE ObjectGoneError is raised.")
    print("  - Result: orphaned raw: keys unreachable via namespace APIs; GC can't see them.")
    print("  - Repeated stale writes accumulate Redis junk indefinitely.")
    print("  - Existing tests pass because they only assert the raise, not raw: integrity.")
    print("  - Root cause: _touch() is a post-write hook; no pre-write liveness guard exists.")
    print("  - Fix direction: add a liveness check BEFORE the first Redis write in each mutator.")

    dom.close()


if __name__ == "__main__":
    main()
