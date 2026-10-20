"""GPDC garbage collection: scan and clean orphaned Redis keys.

Uses per-canonical locking during processing so that users who hold
``obj.lock()`` are not raced against. See
``dev_docs/2026-06-21-cross-process-locking-design.md``.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    import redis

from .exceptions import LockAcquisitionTimeout
from .keys import (
    make_lock_key,
    make_lock_notify_key,
    make_object_key,
    make_object_key_prefix,
    make_raw_key,
    make_raw_key_prefix,
    make_ref_key,
    make_ref_key_prefix,
)
from .locking import acquire
from .meta import ObjectMeta
from .refset import remove_referrer, get_referrers

logger = logging.getLogger(__name__)


def _parse_canonical_from_key(key: str, obj_prefix: str, raw_prefix: str, ref_prefix: str) -> str | None:
    """Return the canonical object name encoded in a gpdc obj/raw/ref key."""
    if key.startswith(obj_prefix):
        return key[len(obj_prefix) :]
    if key.startswith(raw_prefix):
        return key[len(raw_prefix) :]
    if key.startswith(ref_prefix):
        return key[len(ref_prefix) :]
    return None


def _scan_all_keys(
    r: "redis.Redis",
    obj_prefix: str,
    raw_prefix: str,
    ref_prefix: str,
) -> tuple[list[str], list[str], list[str]]:
    """SCAN the three key families without any locking."""
    obj_keys: list[str] = []
    raw_keys: list[str] = []
    ref_keys: list[str] = []

    for prefix, sink in (
        (obj_prefix, obj_keys),
        (raw_prefix, raw_keys),
        (ref_prefix, ref_keys),
    ):
        cursor = 0
        while True:
            # decode_responses=True client: SCAN results are str at runtime
            # (the redis-py stubs say bytes | str — see refset.py).
            cursor, keys = r.scan(cursor=cursor, match=f"{prefix}*", count=100)
            sink.extend(cast("list[str]", keys))
            if cursor == 0:
                break

    return obj_keys, raw_keys, ref_keys


def _gc_process_obj_key(r: "redis.Redis", obj_key: str, raw_to_obj: dict[str, str]) -> int:
    """Process a single obj key. Returns count of deletions."""
    key_type = r.type(obj_key)

    if key_type == "hash":
        meta = ObjectMeta.load(r, obj_key)
        if meta is None:
            return 0
        if not meta.is_collection():
            return 0
        raw_key = meta.value
        raw_to_obj[raw_key] = obj_key
        if not r.exists(raw_key):
            if meta.item_count is not None and meta.item_count > 0:
                logger.warning(
                    "Corrupted container: %s has item_count=%d but raw key %s missing",
                    obj_key,
                    meta.item_count,
                    raw_key,
                )
            return 0

    elif key_type == "string":
        # An alias binding: obj:alias -> "ref:<canonical_obj_key>". Deleting it when its
        # canonical target is gone is correct GC, NOT a race or dangling-reference risk:
        # a container element never stores a ref to an *alias* binding — elements store
        # "ref:<canonical_owner_obj_key>", so the only thing referencing an alias is the
        # name→canonical mapping itself. See docs/lifecycle.md §10 (GC) and container.py
        # _encode_as_ref_value / namespace.py _setitem_container_ref.
        ref_value = cast("str | None", r.get(obj_key))
        if not ref_value or not ref_value.startswith("ref:"):
            r.delete(obj_key)
            return 1
        canonical_obj_key = ref_value[len("ref:") :]
        if not r.exists(canonical_obj_key):
            r.delete(obj_key)
            return 1

    return 0


def _gc_process_ref_key(
    r: "redis.Redis",
    domain: str,
    namespace: str,
    ref_key: str,
    name: str,
) -> int:
    """Process a single ref key. Returns count of deletions."""
    raw_key = make_raw_key(domain, namespace, name)
    obj_key = make_object_key(domain, namespace, name)
    if not r.exists(raw_key) and not r.exists(obj_key):
        r.delete(ref_key)
        return 1

    members = get_referrers(r, ref_key)
    for member in members:
        member_obj_key = make_object_key(domain, namespace, member)
        member_type = r.type(member_obj_key)
        if member_type == "string":
            member_ref = cast("str | None", r.get(member_obj_key))
            if not member_ref or not member_ref.startswith("ref:"):
                remove_referrer(r, ref_key, member)
                continue
            member_target = member_ref[len("ref:") :]
            if member_target != obj_key:
                remove_referrer(r, ref_key, member)
        elif member_type == "hash":
            member_meta = ObjectMeta.load(r, member_obj_key)
            if member_meta is None or not member_meta.is_collection():
                remove_referrer(r, ref_key, member)
                continue
            expected_ref = f"ref:{obj_key}"
            still_refs = False
            member_raw_key = member_meta.value
            member_raw_type = r.type(member_raw_key) if member_raw_key else "none"
            if member_raw_type == "hash":
                for val in r.hvals(member_raw_key):
                    if val == expected_ref:
                        still_refs = True
                        break
            elif member_raw_type == "list":
                for val in r.lrange(member_raw_key, 0, -1):
                    if val == expected_ref:
                        still_refs = True
                        break
            if not still_refs:
                remove_referrer(r, ref_key, member)
        else:
            remove_referrer(r, ref_key, member)

    return 0


def _gc_process_raw_key(
    r: "redis.Redis",
    domain: str,
    namespace: str,
    raw_key: str,
    name: str,
    raw_to_obj: dict[str, str],
) -> int:
    """Process a single raw key. Returns count of deletions (raw + ref)."""
    if raw_key in raw_to_obj:
        return 0
    ref_key = make_ref_key(domain, namespace, name)
    referrers = get_referrers(r, ref_key)
    if not referrers:
        r.delete(raw_key, ref_key)
        return 2
    has_valid_referrer = False
    for ref_name in referrers:
        check_obj_key = make_object_key(domain, namespace, ref_name)
        if r.exists(check_obj_key):
            has_valid_referrer = True
            break
    if not has_valid_referrer:
        r.delete(raw_key, ref_key)
        return 2
    return 0


def _gc_one_canonical(
    r: "redis.Redis",
    domain: str,
    namespace: str,
    name: str,
    obj_keys: list[str],
    ref_keys: list[str],
    raw_keys: list[str],
    gc_lock_ttl: int,
    gc_lock_wait: float,
) -> int:
    """Process all keys belonging to one canonical name under its lock."""
    lock_key = make_lock_key(domain, namespace, name)
    notify_key = make_lock_notify_key(domain, namespace, name)

    try:
        held = acquire(r, lock_key, notify_key, ttl=gc_lock_ttl, timeout=gc_lock_wait)
    except LockAcquisitionTimeout:
        logger.debug("GC skip %r: lock contention", name)
        return 0

    try:
        deleted = 0
        raw_to_obj: dict[str, str] = {}
        for obj_key in obj_keys:
            deleted += _gc_process_obj_key(r, obj_key, raw_to_obj)
        for ref_key in ref_keys:
            deleted += _gc_process_ref_key(r, domain, namespace, ref_key, name)
        for raw_key in raw_keys:
            deleted += _gc_process_raw_key(r, domain, namespace, raw_key, name, raw_to_obj)
        return deleted
    finally:
        held.release()


def run_gc_for_namespace(r: "redis.Redis", domain: str, namespace: str) -> int:
    """Scan one namespace and remove orphaned obj/raw/ref keys.

    Returns the number of keys deleted.

    Per-canonical locking: each canonical name is processed inside an
    exclusive lock (acquired with a short blocking timeout via
    ``gc_lock_wait_seconds``). If the lock cannot be acquired within that
    timeout, that canonical is skipped in this GC cycle.
    """
    obj_prefix = make_object_key_prefix(domain, namespace)
    raw_prefix = make_raw_key_prefix(domain, namespace)
    ref_prefix = make_ref_key_prefix(domain, namespace)

    obj_keys, raw_keys, ref_keys = _scan_all_keys(r, obj_prefix, raw_prefix, ref_prefix)

    # Group keys by canonical name.
    grouped: dict[str, dict[str, list[str]]] = defaultdict(lambda: {"obj": [], "ref": [], "raw": []})
    for key in obj_keys:
        name = _parse_canonical_from_key(key, obj_prefix, raw_prefix, ref_prefix)
        if name is not None:
            grouped[name]["obj"].append(key)
    for key in ref_keys:
        name = _parse_canonical_from_key(key, obj_prefix, raw_prefix, ref_prefix)
        if name is not None:
            grouped[name]["ref"].append(key)
    for key in raw_keys:
        name = _parse_canonical_from_key(key, obj_prefix, raw_prefix, ref_prefix)
        if name is not None:
            grouped[name]["raw"].append(key)

    # Read GC lock params from the Redis meta. We don't have access to the
    # domain object here; the meta is the source of truth.
    from .keys import make_domain_meta_key

    meta = r.hgetall(make_domain_meta_key(domain))
    gc_lock_ttl = int(meta.get("gc_lock_ttl_seconds", "30"))
    gc_lock_wait = float(meta.get("gc_lock_wait_seconds", "5"))

    deleted = 0
    for name in sorted(grouped):
        keys = grouped[name]
        deleted += _gc_one_canonical(
            r,
            domain,
            namespace,
            name,
            keys["obj"],
            keys["ref"],
            keys["raw"],
            gc_lock_ttl,
            gc_lock_wait,
        )
    return deleted
