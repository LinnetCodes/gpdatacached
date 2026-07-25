"""Detailed walkthrough of Finding inconsistency.01: expire_in(0) is broken.

Run:
    .venv/Scripts/python.exe reviews/verification/demo_inconsistency_01_detail.py

Shows what expire_in(0) ACTUALLY does vs what the docs imply, and contrasts it
with the sibling expire_at(0) which already handles zero differently.
No source files modified.
"""
from __future__ import annotations

import datetime

import redis as redis_lib

from gpdatacached.config import GPDCDomainConfig

HOST, PORT, DB, PWD = "127.0.0.1", 6379, 1, "123456"
DOMAIN, NS = "inc01detail", "ns"


def r():
    return redis_lib.Redis(host=HOST, port=PORT, db=DB, password=PWD, decode_responses=True)


def banner(t):
    print("\n" + "=" * 80)
    print(t)
    print("=" * 80)


def show(label, value):
    print(f"    {label}: {value!r}")


def obj_state(name, step):
    from gpdatacached.keys import make_object_key

    rr = r()
    key = make_object_key(DOMAIN, NS, name)
    meta = rr.hgetall(key)
    print(f"  {step}")
    show("meta.cache_mode", meta.get("cache_mode"))
    show("meta.ttl (declared_ttl)", meta.get("ttl"))
    show("Redis TTL on obj: key", rr.ttl(key))  # -1 = persistent, -2 = gone, >0 = seconds
    exists = rr.exists(key)
    show("obj: key still EXISTS?", bool(exists))


def main():
    banner("inconsistency.01 — expire_in(0) persists an INVALID ttl-mode policy")
    r().flushdb()
    from gpdatacached.domain import GPDCDomain

    cfg = GPDCDomainConfig(redis_host=HOST, redis_port=PORT, redis_db=DB, redis_password=PWD)
    dom = GPDCDomain(cfg, name=DOMAIN)
    ns = dom.ensure_namespace(NS)

    # ── SETUP: a valid ttl object ────────────────────────────────────────────
    print("\n[1] ns['x'] = 42; switch to ttl mode with ttl=60 -> a normal, valid ttl object")
    ns["x"] = 42
    obj = ns.get_object("x")
    obj.update_cache_mode_and_ttl("ttl", 60)  # expire_in requires the object already be in ttl mode
    obj_state("x", "after switch to ttl/60  (a healthy ttl object: mode=ttl, ttl=60, Redis TTL≈60)")

    # ── THE BUG: expire_in(0) ────────────────────────────────────────────────
    print("\n[2] Now call expire_in(0) — the caller is asking for 'expire NOW / immediately'")
    print("    >>> What the caller expects: the object should expire (vanish soon, or immediately).")
    print("    >>> What actually happens:")
    obj.expire_in(0)  # succeeds, no error
    obj_state("x", "after expire_in(0)")
    print("  >>> READ THE ABOVE:")
    print("  >>>   cache_mode = 'ttl'        (still says TTL mode)")
    print("  >>>   declared_ttl = 0          (invalid: docs say ttl-mode requires ttl > 0)")
    print("  >>>   Redis TTL = -1            (PERSISTENT! the object will NEVER expire)")
    print("  >>> So asking to 'expire now' made the object PERMANENT. The exact opposite of intent.")

    # ── WHY this happens (code path) ─────────────────────────────────────────
    print("\n[3] Why — the code path (src/gpdatacached/object.py:216-224):")
    print("      def expire_in(self, seconds):")
    print("          ...")
    print("          policy = TTLPolicy(cache_mode='ttl', declared_ttl=seconds)  # NO strict validation")
    print("          self.set_lifecycle(policy)")
    print("    >>> TTLPolicy.__post_init__ only calls _validate_basic (allows ttl=0).")
    print("    >>> The STRICT check validate_cache_mode_and_ttl (requires ttl>0) is bypassed here.")
    print("    >>> For (ttl, 0), policy.expire_seconds returns None -> apply_ttl calls PERSIST.")

    # ── THE DOCUMENTED INVARIANT THIS VIOLATES ───────────────────────────────
    print("\n[4] The documented invariant this violates (docs/en/lifecycle.md, config validation):")
    print("      validate_cache_mode_and_ttl: 'cache_mode=ttl requires ttl > 0, got 0'")
    print("    >>> Every OTHER user-facing path (update_cache_mode_and_ttl, GPDCDomainConfig,")
    print("    >>> GPDCNamespaceConfig) enforces ttl > 0 for ttl/sliding. expire_in is the only")
    print("    >>> hole. So Redis can now hold a state the docs say can never be written.")

    # ── THE SIBLING THAT DISAGREES: expire_at(0) ─────────────────────────────
    print("\n[5] The sibling API 'expire_at' ALREADY handles zero differently (object.py:233-243):")
    print("    >>> expire_at treats seconds==0 as 'delete immediately' (del ns[canonical]).")
    ns["y"] = 99
    obj_y = ns.get_object("y")
    obj_y.update_cache_mode_and_ttl("ttl", 60)
    obj_state("y", "y after switch to ttl/60")
    print("\n    Now call obj_y.expire_at(<a past timestamp>) -> equivalent to seconds==0:")
    past = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=10)
    obj_y.expire_at(past)
    obj_state("y", "y after expire_at(past)  (DELETED: obj: gone, Redis TTL=-2)")
    print("  >>> So expire_at(0) DELETES the object, while expire_in(0) makes it PERMANENT.")
    print("  >>> Two verbs named 'expire_*' do OPPOSITE things for the same 'zero' input.")

    # ── VERDICT ──────────────────────────────────────────────────────────────
    banner("SUMMARY")
    print("  - expire_in(0) does NOT expire the object; it PERSISTS it (Redis TTL -1).")
    print("  - The resulting meta (cache_mode=ttl, declared_ttl=0) violates the project's own")
    print("    invariant that ttl/sliding require ttl > 0.")
    print("  - expire_at's zero-case deletes the object; expire_in's zero-case keeps it forever.")
    print("    The two sibling verbs disagree on what 'zero' means.")
    print("  - Decision needed: should expire_in(0) (a) raise, (b) delete immediately like")
    print("    expire_at, or (c) something else? Today it silently does the wrong thing.")

    dom.close()


if __name__ == "__main__":
    main()
