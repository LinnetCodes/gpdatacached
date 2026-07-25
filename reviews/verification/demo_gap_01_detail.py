"""Detailed walkthrough of Finding gap.01: take_ownership() is a no-op.

Run:
    .venv/Scripts/python.exe reviews/verification/demo_gap_01_detail.py

This demo exposes the Redis state at every step so the maintainer can see
exactly what the code does vs. what the docs promise. No source files modified.
"""
from __future__ import annotations

import redis as redis_lib

from gpdatacached.config import GPDCDomainConfig

HOST, PORT, DB, PWD = "127.0.0.1", 6379, 1, "123456"
DOMAIN, NS = "gap01detail", "ns"


def r():
    return redis_lib.Redis(host=HOST, port=PORT, db=DB, password=PWD, decode_responses=True)


def banner(t):
    print("\n" + "=" * 80)
    print(t)
    print("=" * 80)


def show(label, value):
    print(f"    {label}: {value!r}")


def state(step):
    """Dump the full relevant Redis state: original obj meta, alias binding, ref counts."""
    from gpdatacached.keys import make_object_key, make_ref_key

    rr = r()
    orig_key = make_object_key(DOMAIN, NS, "original")
    alias_key = make_object_key(DOMAIN, NS, "alias")
    ref_key = make_ref_key(DOMAIN, NS, "original")
    print(f"  {step}")
    show("alias binding (obj:alias value)", rr.get(alias_key))
    orig_meta = rr.hgetall(orig_key)
    show("original meta.owner", orig_meta.get("owner"))
    show("original meta.cache_mode", orig_meta.get("cache_mode"))
    show("original meta.ttl (declared_ttl)", orig_meta.get("ttl"))
    show("original ref: (referrers -> counts)", rr.hgetall(ref_key))
    show("original Redis TTL (obj:original)", rr.ttl(orig_key))


def main():
    banner("gap.01 — take_ownership() is a no-op for named references")
    r().flushdb()
    from gpdatacached.domain import GPDCDomain

    cfg = GPDCDomainConfig(redis_host=HOST, redis_port=PORT, redis_db=DB, redis_password=PWD)
    dom = GPDCDomain(cfg, name=DOMAIN)
    ns = dom.ensure_namespace(NS)

    # ── STEP 1: create the canonical object and set a real TTL ──────────────
    print("\n[1] ns['original'] = [1, 2, 3]; original.set ttl=60 (cache_mode='ttl')")
    ns["original"] = [1, 2, 3]
    ns.get_object("original").update_cache_mode_and_ttl("ttl", 60)
    state("after step 1")

    # ── STEP 2: create an alias ──────────────────────────────────────────────
    print("\n[2] ns['alias'] = ns['original']   # alias is a named reference")
    ns["alias"] = ns["original"]
    state("after step 2  (note: alias just points to the same canonical; ref count goes 1)")

    print("\n  >>> The alias object handle:")
    alias_obj = ns.get_object("alias")
    show("alias_obj.name", alias_obj.name)
    show("alias_obj.canonical_name", alias_obj.canonical_name)
    show("alias_obj.is_reference", alias_obj.is_reference)
    show("alias_obj.uri", alias_obj.uri)
    print("  >>> KEY INSIGHT: alias_obj.canonical_name == 'original'.")
    print("  >>> There is NO alias-owned metadata in Redis. The alias is just a")
    print("  >>> string pointer (obj:alias -> 'ref:obj:original') plus a refcount")
    print("  >>> entry. So 'the alias owning its own data' has no representation")
    print("  >>> in the current storage model.")

    # ── STEP 3: docs promise — call take_ownership() ─────────────────────────
    print("\n[3] alias_obj.take_ownership()   # docs: 'alias becomes self-owned, owner=\"alias\"'")
    print("    >>> docs/en/lifecycle.md:216-219 promises this.")
    alias_obj.take_ownership()
    state("after step 3  (EXPECTED per docs: owner='alias'; ACTUAL: owner='original', nothing changed)")

    # ── STEP 4: prove the lifecycle is still shared ──────────────────────────
    print("\n[4] Now change lifecycle through the ALIAS — docs say it should be independent")
    print("    alias_obj.update_cache_mode_and_ttl('permanent', 0)")
    alias_obj.update_cache_mode_and_ttl("permanent", 0)
    state("after step 4  (if alias were independent, original would STILL be ttl=60;")
    print("                  ACTUAL: original became permanent too — they share one policy)")

    print("\n  >>> CONCLUSION:")
    print("  >>> - meta.owner never changed ('original' -> 'original').")
    print("  >>> - Mutating the alias's lifecycle mutated the CANONICAL object.")
    print("  >>> - So aliases and originals always share one lifecycle, the OPPOSITE")
    print("  >>>   of the documented 'independent ownership'.")
    print("  >>> - The existing unit test (tests/test_objects.py) asserts the no-op")
    print("  >>>   value, i.e. it encodes the same bug the code has — a passing test")
    print("  >>>   is NOT evidence of design intent here.")

    # ── STEP 5: walk the code path that makes it a no-op ─────────────────────
    print("\n[5] Why the code is a no-op (src/gpdatacached/object.py:178-193):")
    print("      def take_ownership(self):")
    print("          ...")
    print("          if meta.owner == self.canonical_name:   # 190: canonical='original', owner='original' -> TRUE")
    print("              return                               # 191: returns immediately, nothing written")
    print("          meta.with_updates(owner=self.canonical_name).save(...)  # 193: would set owner='original' anyway")

    dom.close()
    banner("gap.01 demo done")


if __name__ == "__main__":
    main()
