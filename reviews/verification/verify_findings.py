"""Reproduction demos for the findings in CQR-2026-07-24.

Run with the repo venv:
    .venv/Scripts/python.exe reviews/verification/verify_findings.py

Uses the disposable test Redis (127.0.0.1:6379, DB 1, password 123456) and
flushes DB 1 before each finding. Each finding prints a PASS/FAIL/SHOW banner
and the raw Redis state it observed. This script does NOT modify any source
file — it only exercises the public API.
"""
from __future__ import annotations

import sys
import time

import redis as redis_lib

from gpdatacached.config import GPDCDomainConfig

REDIS_HOST = "127.0.0.1"
REDIS_PORT = 6379
REDIS_DB = 1
REDIS_PASSWORD = "123456"


def raw():
    return redis_lib.Redis(
        host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, password=REDIS_PASSWORD, decode_responses=True
    )


def banner(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def flush():
    raw().flushdb()


def make_domain(name="verify"):
    d = GPDCDomainConfig(
        redis_host=REDIS_HOST, redis_port=REDIS_PORT, redis_db=REDIS_DB, redis_password=REDIS_PASSWORD
    )
    from gpdatacached.domain import GPDCDomain

    dom = GPDCDomain(d, name=name)
    return dom


# ──────────────────────────────────────────────────────────────────────────────
# logic.01 — failed multi-element encodes leave partial writes / phantom refs
# ──────────────────────────────────────────────────────────────────────────────
def demo_logic_01():
    banner("logic.01 — failed encode leaves partial parent writes / phantom refs")
    flush()
    from gpdatacached.builtins.list_object import ListObject

    dom = make_domain("logic01")
    ns = dom.ensure_namespace("ns")

    # --- Case A: DictObject.update writes pairs one at a time; a later pair fails ---
    print("\n[A] DictObject.update({'ok': [1,2], 'bad': object()})")
    ns["d"] = {"placeholder": 0}
    d = ns["d"]
    r = raw()
    raw_hgetall_before = r.hgetall(d.raw_key)
    try:
        d.update({"ok": [1, 2], "bad": object()})
        print("  update() did NOT raise — unexpected")
    except TypeError as e:
        print(f"  update() raised TypeError as expected: {e}")
    raw_hgetall_after = r.hgetall(d.raw_key)
    print(f"  parent dict raw BEFORE update: {raw_hgetall_before}")
    print(f"  parent dict raw AFTER  update: {raw_hgetall_after}")
    ok_val = raw_hgetall_after.get("str:ok")
    print(f"  -> 'str:ok' field present after failure? {ok_val is not None}")
    if ok_val and ok_val.startswith("ref:"):
        # check if the anonymous object it points to still exists
        anon_obj_key = ok_val[len("ref:"):]
        print(f"  -> referenced anon obj:{anon_obj_key} exists? {r.exists(anon_obj_key)}")
        # The report claims the anon obj: key is DELETED by rollback, leaving a dangling ref.
        print(f"  -> DANGLING ref (parent points to a deleted anon)? {not r.exists(anon_obj_key)}")

    # --- Case B: ListObject.extend with a pre-existing child + a bad element ---
    print("\n[B] list.extend([existing_child, object()])")
    ns["child"] = [10, 20]
    child = ns["child"]  # existing container
    ns["parent"] = []  # parent list
    parent = ns["parent"]
    # find child's canonical obj key + ref key
    from gpdatacached.keys import make_object_key, make_ref_key

    child_canonical = child.canonical_name
    child_ref_key = make_ref_key("logic01", "ns", child_canonical)
    refs_before = r.hgetall(child_ref_key)
    try:
        parent.extend([child, object()])
        print("  extend() did NOT raise — unexpected")
    except TypeError as e:
        print(f"  extend() raised TypeError as expected: {e}")
    refs_after = r.hgetall(child_ref_key)
    parent_raw = r.lrange(parent.raw_key, 0, -1)
    print(f"  child ref: BEFORE extend: {refs_before}")
    print(f"  child ref: AFTER  extend: {refs_after}")
    print(f"  parent raw list AFTER extend: {parent_raw}")
    phantom = (not parent_raw) and ("parent" in refs_after)
    print(f"  -> PHANTOM referrer (parent in child's ref: but parent list empty)? {phantom}")

    dom.close()


# ──────────────────────────────────────────────────────────────────────────────
# logic.02 — stale handle mutates Redis before raising ObjectGoneError
# ──────────────────────────────────────────────────────────────────────────────
def demo_logic_02():
    banner("logic.02 — stale handle mutates raw: before ObjectGoneError")
    flush()
    from gpdatacached.exceptions import ObjectGoneError

    dom = make_domain("logic02")
    ns = dom.ensure_namespace("ns")
    r = raw()

    ns["lst"] = [1]
    lst = ns["lst"]  # retain handle
    raw_key = lst.raw_key
    print(f"  raw_key = {raw_key}")
    print(f"  raw BEFORE stale append: {r.lrange(raw_key, 0, -1)}")
    # delete the binding + object (canonical delete)
    del ns["lst"]
    # The canonical meta is gone; the handle is now stale.
    try:
        lst.append(2)
        print("  stale append() did NOT raise — unexpected")
    except ObjectGoneError as e:
        print(f"  stale append() raised ObjectGoneError as expected: {e}")
    raw_after = r.lrange(raw_key, 0, -1)
    raw_exists = r.exists(raw_key)
    print(f"  raw EXISTS after stale append raised? {raw_exists}")
    print(f"  raw VALUES after stale append raised: {raw_after}")
    print(f"  -> stale handle wrote int:2 to an unreachable raw key? {raw_exists and raw_after == ['int:2']}")
    dom.close()


# ──────────────────────────────────────────────────────────────────────────────
# logic.03 — non-unit RangeIndex interpreted as step==1
# ──────────────────────────────────────────────────────────────────────────────
def demo_logic_03():
    banner("logic.03 — non-unit RangeIndex treated as step==1")
    flush()
    import pandas as pd
    from gpdatacached.extensions.pandas_support import register as register_pandas

    register_pandas()

    dom = make_domain("logic03")
    ns = dom.ensure_namespace("ns")
    r = raw()

    # --- DataFrame __len__ with RangeIndex(5, 20, 5) → [5,10,15], 3 rows ---
    print("\n[A] DataFrame indexed by RangeIndex(5, 20, 5)")
    df = pd.DataFrame({"v": [10, 20, 30]}, index=pd.RangeIndex(5, 20, 5))
    print(f"  source DataFrame: rows={len(df)}, index={list(df.index)}")
    ns["df"] = df
    cached_df = ns["df"]
    reported_len = len(cached_df)
    print(f"  len(cached_df) reported: {reported_len}  (true = 3)")
    print(f"  -> wrong row count? {reported_len != 3}")
    # column assignment rejected as length-mismatch?
    col_assign_failed = False
    err = None
    try:
        cached_df.value  # noqa
    except Exception as e:
        err = e
    try:
        cached_df["w"] = [100, 200, 300]  # correct length 3
        print("  3-value column assignment SUCCEEDED")
    except Exception as e:
        col_assign_failed = True
        err = e
        print(f"  3-value column assignment REJECTED: {type(e).__name__}: {e}")
    print(f"  -> correct-length column assignment wrongly rejected? {col_assign_failed}")

    # --- Series extend with RangeIndex step != 1 ---
    print("\n[B] Series extend on RangeIndex(5, 20, 5)")
    s = pd.Series([10, 20, 30], index=pd.RangeIndex(5, 20, 5), name="x")
    ns["s"] = s
    cached_s = ns["s"]
    print(f"  source Series: len={len(s)}, index={list(s.index)}")
    appended = pd.Series([40, 50, 60, 70, 80], index=pd.RangeIndex(20, 45, 5), name="x")
    try:
        cached_s.extend(appended)
        print("  extend() succeeded")
    except Exception as e:
        print(f"  extend() raised: {type(e).__name__}: {e}")
    # now try to materialize
    materialize_err = None
    try:
        val = cached_s.value
        print(f"  .value materialized OK: len={len(val)}, index={list(val.index)}")
    except Exception as e:
        materialize_err = e
        print(f"  .value raised {type(e).__name__}: {e}")
    print(f"  -> Series in inconsistent state after extend? {materialize_err is not None}")

    dom.close()


# ──────────────────────────────────────────────────────────────────────────────
# inconsistency.01 — expire_in(0) persists an invalid ttl-mode policy
# ──────────────────────────────────────────────────────────────────────────────
def demo_inconsistency_01():
    banner("inconsistency.01 — expire_in(0) persists ttl-mode policy with declared_ttl=0")
    flush()
    dom = make_domain("inc01")
    ns = dom.ensure_namespace("ns")
    r = raw()

    ns["x"] = 42
    obj = ns.get_object("x")
    # set to a valid ttl first
    obj.update_cache_mode_and_ttl("ttl", 60)
    from gpdatacached.keys import make_object_key

    obj_key = make_object_key("inc01", "ns", "x")
    ttl_before = r.ttl(obj_key)
    meta_before = r.hgetall(obj_key)
    print(f"  BEFORE expire_in(0): ttl={ttl_before}, cache_mode={meta_before.get('cache_mode')}, declared_ttl={meta_before.get('ttl')}")
    try:
        obj.expire_in(0)
        print("  expire_in(0) succeeded (no validation error)")
    except Exception as e:
        print(f"  expire_in(0) raised: {type(e).__name__}: {e}")
    ttl_after = r.ttl(obj_key)
    meta_after = r.hgetall(obj_key)
    print(f"  AFTER  expire_in(0): ttl={ttl_after} (-1 = persistent), cache_mode={meta_after.get('cache_mode')}, declared_ttl={meta_after.get('ttl')}")
    persisted = ttl_after == -1
    print(f"  -> object now PERSISTENT (ttl=-1) while cache_mode says 'ttl'? {persisted}")
    dom.close()


# ──────────────────────────────────────────────────────────────────────────────
# inconsistency.02 — SeriesObject.name hides the base binding identity
# ──────────────────────────────────────────────────────────────────────────────
def demo_inconsistency_02():
    banner("inconsistency.02 — SeriesObject.name returns pandas payload name, not binding")
    flush()
    import pandas as pd
    from gpdatacached.extensions.pandas_support import register as register_pandas

    register_pandas()

    dom = make_domain("inc02")
    ns = dom.ensure_namespace("ns")

    s = pd.Series([1, 2, 3], name="payload_name")
    ns["binding"] = s
    obj = ns.get_object("binding")
    print(f"  object.name           = {obj.name!r}")
    print(f"  object.canonical_name = {obj.canonical_name!r}")
    print(f"  object.uri            = {obj.uri!r}")
    mismatch = obj.name != obj.canonical_name and obj.uri.endswith("/binding")
    print(f"  -> .name != binding identity while .uri says binding? {mismatch}")
    dom.close()


# ──────────────────────────────────────────────────────────────────────────────
# inconsistency.03 — closed-cache __contains__ disagrees
# ──────────────────────────────────────────────────────────────────────────────
def demo_inconsistency_03():
    banner("inconsistency.03 — __contains__ works after close(), siblings raise")
    flush()
    from pathlib import Path

    from gpconfig import GPConfigManager

    configs_dir = Path("tests/configs")
    mgr = GPConfigManager("gpdatacached", cfg_folder=str(configs_dir))
    folder = mgr.get_config("gpdc")
    import gpdatacached  # noqa: F401

    from gpdatacached.data_cache import GPDataCache

    cache = GPDataCache(folder)
    known = "core_domain"
    print(f"  '{known}' in cache (open): {known in cache}")
    cache.get_domain(known)  # construct it
    cache.close()
    contains_after_close = known in cache  # does this raise or return True?
    print(f"  '{known}' in cache (CLOSED): returned {contains_after_close!r} (no RuntimeError)")
    # now test that a sibling raises
    sibling_raised = False
    sibling_err = None
    try:
        cache.get_domain(known)
    except RuntimeError as e:
        sibling_raised = True
        sibling_err = e
        print(f"  cache.get_domain('{known}') on CLOSED cache raised RuntimeError: {e}")
    has_uri_raised = False
    try:
        cache.has_uri(f"gpdc://{known}/core_app/x")
    except RuntimeError as e:
        has_uri_raised = True
        print(f"  cache.has_uri(...) on CLOSED cache raised RuntimeError: {e}")
    print(f"  -> __contains__ returns value after close while get_domain & has_uri raise? "
          f"{bool(contains_after_close) and (sibling_raised or has_uri_raised)}")


# ──────────────────────────────────────────────────────────────────────────────
# gap.01 — take_ownership() is a no-op for named references
# ──────────────────────────────────────────────────────────────────────────────
def demo_gap_01():
    banner("gap.01 — take_ownership() no-op for named reference")
    flush()
    dom = make_domain("gap01")
    ns = dom.ensure_namespace("ns")
    r = raw()

    ns["original"] = [1, 2, 3]
    ns["alias"] = ns["original"]  # alias is a named reference
    alias_obj = ns.get_object("alias")
    from gpdatacached.keys import make_object_key

    canonical_key = make_object_key("gap01", "ns", "original")
    meta_before = r.hgetall(canonical_key)
    print(f"  canonical_name of alias: {alias_obj.canonical_name!r}")
    print(f"  alias.name: {alias_obj.name!r}")
    print(f"  meta.owner BEFORE take_ownership: {meta_before.get('owner')!r}")
    alias_obj.take_ownership()
    meta_after = r.hgetall(canonical_key)
    print(f"  meta.owner AFTER  take_ownership: {meta_after.get('owner')!r}")
    owner_unchanged = meta_before.get("owner") == meta_after.get("owner") == "original"
    print(f"  -> owner unchanged (still canonical 'original', NOT 'alias')? {owner_unchanged}")

    # now mutate lifecycle via the alias and confirm it affects the canonical
    print("\n  Lifecycle mutation via alias:")
    try:
        alias_obj.update_cache_mode_and_ttl("ttl", 30)
        meta_after_lc = r.hgetall(canonical_key)
        print(f"  canonical meta.cache_mode after alias.update_cache_mode_and_ttl('ttl',30): "
              f"{meta_after_lc.get('cache_mode')!r}")
        print(f"  -> alias lifecycle mutation changed the CANONICAL object's policy? "
              f"{meta_after_lc.get('cache_mode') == 'ttl'}")
    except Exception as e:
        print(f"  alias lifecycle mutation raised: {type(e).__name__}: {e}")
    dom.close()


# ──────────────────────────────────────────────────────────────────────────────
# perf.01 — gc_interval_seconds=0 unbounded GC loop
# ──────────────────────────────────────────────────────────────────────────────
def demo_perf_01():
    banner("perf.01 — gc_interval_seconds=0 spins the GC loop")
    flush()

    cfg = GPDCDomainConfig(
        redis_host=REDIS_HOST,
        redis_port=REDIS_PORT,
        redis_db=REDIS_DB,
        redis_password=REDIS_PASSWORD,
        gc_enabled=True,
        gc_interval_seconds=0,
    )
    from gpdatacached.domain import GPDCDomain

    dom = GPDCDomain(cfg, name="perf01")
    # The hot loop is `_gc_stop_event.wait(timeout=interval)`. With interval=0,
    # wait(0) returns False immediately, so the while condition stays true and the
    # loop spins with no sleep. Instrument the event's wait to count spins.
    counts = {"wait": 0}
    orig_wait = dom._gc_stop_event.wait

    def counting_wait(timeout=None):
        counts["wait"] += 1
        return orig_wait(timeout)

    dom._gc_stop_event.wait = counting_wait
    time.sleep(0.05)  # 50 ms
    dom.close()
    print(f"  Event.wait(0) calls in ~50ms with gc_interval_seconds=0: {counts['wait']}")
    print(f"  -> hot loop (many spin iterations, no delay)? {counts['wait'] > 50}")

    # also test negative — Event.wait(negative) raises ValueError -> daemon dies
    print("\n  Negative interval:")
    cfg_neg = GPDCDomainConfig(
        redis_host=REDIS_HOST,
        redis_port=REDIS_PORT,
        redis_db=REDIS_DB,
        redis_password=REDIS_PASSWORD,
        gc_enabled=True,
        gc_interval_seconds=-5,
    )
    dom_neg = GPDCDomain(cfg_neg, name="perf01neg")
    time.sleep(0.05)
    thread_alive = dom_neg._gc_thread is not None and dom_neg._gc_thread.is_alive()
    print(f"  with gc_interval_seconds=-5: GC thread alive after 50ms? {thread_alive}")
    print(f"  (threading.Event.wait(negative) raises ValueError -> daemon thread dies)")
    dom_neg.close()


# ──────────────────────────────────────────────────────────────────────────────
# behavior.01 — Pydantic __setitem__ bypasses validation
# ──────────────────────────────────────────────────────────────────────────────
def demo_behavior_01():
    banner("behavior.01 — Pydantic container __setitem__ bypasses field validation")
    flush()
    from gpdatacached import GPDCTypeRegistry
    from gpdatacached.extensions.pydantic_support import (
        PydanticModelContainerCodec,
        BaseModelContainerObject,
    )
    from pydantic import BaseModel

    class Order(BaseModel):
        total: float
        name: str

    class OrderContainerObject(BaseModelContainerObject):
        TYPE = "collection:OrderB01"

    GPDCTypeRegistry.register(
        python_type=Order,
        gpdc_type="collection:OrderB01",
        object_class=OrderContainerObject,
        codec_class=PydanticModelContainerCodec,
    )

    dom = make_domain("beh01")
    ns = dom.ensure_namespace("ns")

    ns["order"] = Order(total=10.0, name="widget")
    obj = ns["order"]
    print(f"  field 'total' is typed float; writing string 'not-a-number'")
    write_raised = None
    try:
        obj["total"] = "not-a-number"
        print("  obj['total'] = 'not-a-number' SUCCEEDED (no Pydantic validation)")
    except Exception as e:
        write_raised = e
        print(f"  obj['total'] = 'not-a-number' raised: {type(e).__name__}: {e}")
    # read back raw (no validation)
    if write_raised is None:
        readback = obj["total"]
        print(f"  obj['total'] read back: {readback!r} (type {type(readback).__name__})")
    # materialize the full model — should raise a Pydantic ValidationError
    materialize_err = None
    try:
        val = obj.value
        print(f"  obj.value materialized: {val}")
    except Exception as e:
        materialize_err = e
        print(f"  obj.value raised {type(e).__name__}: {str(e)[:120]}")
    deferred = write_raised is None and materialize_err is not None
    print(f"  -> invalid value persisted at write, error DEFERRED to .value read? {deferred}")
    dom.close()


# ──────────────────────────────────────────────────────────────────────────────
# structure.01 — fixture leaks constructed domain (static check, no run needed)
# ──────────────────────────────────────────────────────────────────────────────
def demo_structure_01():
    banner("structure.01 — ns fixture returns without yield / domain.close() (source check)")
    # Source-level finding: confirm the ns fixture in test_nested_containers.py
    # returns the namespace directly without yielding and without closing the domain.
    import pathlib
    import re

    text = pathlib.Path("tests/test_nested_containers.py").read_text(encoding="utf-8")
    # Match the `ns` fixture definition specifically.
    m = re.search(r"@pytest\.fixture\(\)\s*\ndef ns\(r\):.*?(?=\n(?:@|class |\Z))", text, re.DOTALL)
    fixture = m.group(0) if m else "(ns fixture not found)"
    has_yield = "yield" in fixture
    has_close = "domain.close()" in fixture
    print(fixture.rstrip())
    print(f"  fixture uses 'yield'? {has_yield}")
    print(f"  fixture calls 'domain.close()'? {has_close}")
    print(f"  -> domain leaked (no yield, no close)? {(not has_yield) and (not has_close)}")


def main():
    demos = [
        ("logic.01", demo_logic_01),
        ("logic.02", demo_logic_02),
        ("logic.03", demo_logic_03),
        ("inconsistency.01", demo_inconsistency_01),
        ("inconsistency.02", demo_inconsistency_02),
        ("inconsistency.03", demo_inconsistency_03),
        ("gap.01", demo_gap_01),
        ("perf.01", demo_perf_01),
        ("behavior.01", demo_behavior_01),
        ("structure.01", demo_structure_01),
    ]
    only = sys.argv[1:] if len(sys.argv) > 1 else None
    for name, fn in demos:
        if only and not any(name.startswith(x) or x in name for x in only):
            continue
        try:
            fn()
        except Exception as e:
            print(f"\n!!! DEMO {name} crashed: {type(e).__name__}: {e}")
            import traceback

            traceback.print_exc()
    print("\n" + "=" * 78)
    print("Done.")
    print("=" * 78)


if __name__ == "__main__":
    main()
