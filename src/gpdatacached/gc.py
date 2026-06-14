"""GPDC garbage collection: scan and clean orphaned Redis keys."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import redis

from .keys import (
    make_object_key,
    make_object_key_prefix,
    make_raw_key,
    make_raw_key_prefix,
    make_ref_key,
    make_ref_key_prefix,
)
from .meta import ObjectMeta
from .refset import remove_referrer, get_referrers

logger = logging.getLogger(__name__)


def _gc_scan_obj_keys(
    r: "redis.Redis",
    obj_prefix: str,
    raw_to_obj: dict[str, str],
) -> int:
    deleted = 0
    obj_pattern = f"{obj_prefix}*"
    cursor = 0
    while True:
        cursor, keys = r.scan(cursor=cursor, match=obj_pattern, count=100)
        for obj_key in keys:
            key_type = r.type(obj_key)

            if key_type == "hash":
                meta = ObjectMeta.load(r, obj_key)
                if meta is None:
                    continue

                if not meta.is_collection():
                    continue

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
                    continue

            elif key_type == "string":
                ref_value = r.get(obj_key)
                if not ref_value or not ref_value.startswith("ref:"):
                    r.delete(obj_key)
                    deleted += 1
                    continue
                canonical_obj_key = ref_value[len("ref:") :]
                if not r.exists(canonical_obj_key):
                    r.delete(obj_key)
                    deleted += 1
        if cursor == 0:
            break
    return deleted


def _gc_scan_raw_keys(
    r: "redis.Redis",
    domain: str,
    namespace: str,
    raw_prefix: str,
    raw_to_obj: dict[str, str],
) -> int:
    deleted = 0
    raw_pattern = f"{raw_prefix}*"
    cursor = 0
    while True:
        cursor, keys = r.scan(cursor=cursor, match=raw_pattern, count=100)
        for raw_key in keys:
            if raw_key in raw_to_obj:
                continue
            name = raw_key[len(raw_prefix) :]
            ref_key = make_ref_key(domain, namespace, name)
            referrers = get_referrers(r, ref_key)
            if not referrers:
                r.delete(raw_key, ref_key)
                deleted += 2
            else:
                has_valid_referrer = False
                for ref_name in referrers:
                    check_obj_key = make_object_key(domain, namespace, ref_name)
                    if r.exists(check_obj_key):
                        has_valid_referrer = True
                        break
                if not has_valid_referrer:
                    r.delete(raw_key, ref_key)
                    deleted += 2
        if cursor == 0:
            break
    return deleted


def _gc_scan_ref_keys(
    r: "redis.Redis",
    domain: str,
    namespace: str,
    ref_prefix: str,
) -> int:
    deleted = 0
    ref_pattern = f"{ref_prefix}*"
    cursor = 0
    while True:
        cursor, keys = r.scan(cursor=cursor, match=ref_pattern, count=100)
        for ref_key in keys:
            name = ref_key[len(ref_prefix) :]
            raw_key = make_raw_key(domain, namespace, name)
            obj_key = make_object_key(domain, namespace, name)
            if not r.exists(raw_key) and not r.exists(obj_key):
                r.delete(ref_key)
                deleted += 1
                continue
            members = get_referrers(r, ref_key)
            for member in members:
                member_obj_key = make_object_key(domain, namespace, member)
                member_type = r.type(member_obj_key)
                if member_type == "string":
                    member_ref = r.get(member_obj_key)
                    if not member_ref or not member_ref.startswith("ref:"):
                        remove_referrer(r, ref_key, member)
                        continue
                    member_target = member_ref[len("ref:") :]
                    if member_target != obj_key:
                        remove_referrer(r, ref_key, member)
                elif member_type == "hash":
                    member_meta = ObjectMeta.load(r, member_obj_key)
                    if member_meta is None:
                        remove_referrer(r, ref_key, member)
                        continue
                    if not member_meta.is_collection():
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
        if cursor == 0:
            break
    return deleted


def run_gc_for_namespace(r: "redis.Redis", domain: str, namespace: str) -> int:
    """Scan one namespace and remove orphaned obj/raw/ref keys.

    Returns the number of keys deleted.
    """
    obj_prefix = make_object_key_prefix(domain, namespace)
    raw_prefix = make_raw_key_prefix(domain, namespace)
    ref_prefix = make_ref_key_prefix(domain, namespace)

    raw_to_obj: dict[str, str] = {}

    deleted = _gc_scan_obj_keys(r, obj_prefix, raw_to_obj)
    deleted += _gc_scan_ref_keys(r, domain, namespace, ref_prefix)
    deleted += _gc_scan_raw_keys(r, domain, namespace, raw_prefix, raw_to_obj)

    return deleted
