"""GPDC garbage collection: scan and clean orphaned Redis keys."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    import redis


def run_gc_for_namespace(r: "redis.Redis", domain: str, namespace: str) -> int:
    """Scan one namespace and remove orphaned obj/raw/ref keys.

    Returns the number of keys deleted.
    """
    prefix = f"gpdc:{domain}:{namespace}:"
    obj_prefix = f"{prefix}obj:"
    raw_prefix = f"{prefix}raw:"
    ref_prefix = f"{prefix}ref:"

    deleted = 0
    raw_to_obj: dict[str, str] = {}

    # Phase 1: Scan obj:* keys
    obj_pattern = f"{obj_prefix}*"
    cursor = 0
    while True:
        cursor, keys = r.scan(cursor=cursor, match=obj_pattern, count=100)
        for obj_key in keys:
            key_type = r.type(obj_key)

            if key_type == "hash":
                meta = r.hgetall(obj_key)
                obj_type = meta.get("type", "")
                raw_key = meta.get("value", "")

                if not raw_key or not obj_type.startswith("collection:"):
                    continue

                raw_to_obj[raw_key] = obj_key

                if not r.exists(raw_key):
                    ref_key = ref_prefix + obj_key[len(obj_prefix):]
                    r.delete(obj_key, ref_key)
                    deleted += 2

            elif key_type == "string":
                ref_value = r.get(obj_key)
                if not ref_value or not ref_value.startswith("ref:"):
                    r.delete(obj_key)
                    deleted += 1
                    continue
                canonical_obj_key = ref_value[len("ref:"):]
                if not r.exists(canonical_obj_key):
                    r.delete(obj_key)
                    deleted += 1
        if cursor == 0:
            break

    # Phase 2: Scan raw:* keys — find orphaned raw data
    raw_pattern = f"{raw_prefix}*"
    cursor = 0
    while True:
        cursor, keys = r.scan(cursor=cursor, match=raw_pattern, count=100)
        for raw_key in keys:
            if raw_key in raw_to_obj:
                continue
            name = raw_key[len(raw_prefix):]
            ref_key = ref_prefix + name
            referrers = r.smembers(ref_key)
            if not referrers:
                r.delete(raw_key, ref_key)
                deleted += 2
            else:
                has_valid_referrer = False
                for ref_name in referrers:
                    check_obj_key = obj_prefix + ref_name
                    if r.exists(check_obj_key):
                        has_valid_referrer = True
                        break
                if not has_valid_referrer:
                    r.delete(raw_key, ref_key)
                    deleted += 2
        if cursor == 0:
            break

    # Phase 3: Scan ref:* keys — clean stale members and orphaned ref sets
    ref_pattern = f"{ref_prefix}*"
    cursor = 0
    while True:
        cursor, keys = r.scan(cursor=cursor, match=ref_pattern, count=100)
        for ref_key in keys:
            name = ref_key[len(ref_prefix):]
            raw_key = raw_prefix + name
            obj_key = obj_prefix + name
            if not r.exists(raw_key) and not r.exists(obj_key):
                r.delete(ref_key)
                deleted += 1
                continue
            members = r.smembers(ref_key)
            for member in members:
                member_obj_key = obj_prefix + member
                if not r.exists(member_obj_key):
                    r.srem(ref_key, member)
        if cursor == 0:
            break

    return deleted
