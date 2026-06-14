"""Reference set maintenance for GPDC container objects.

Ref sets track which objects reference a given container's raw data.
Uses Redis Hash to track reference counts — each field is a referrer
name, each value is the number of slots referencing the target.

.. note::

    GPDC is designed for single-writer access (no thread safety).
    ``release_ref`` performs ``HINCRBY -1`` then a conditional ``HDEL``
    as two round-trips. Under concurrent writers this could race; if
    multi-writer support is needed, wrap the pair in a Lua script.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import redis


def add_referrer(r: "redis.Redis", ref_key: str, referrer_name: str) -> None:
    """Register a reference from referrer_name (increment count)."""
    r.hincrby(ref_key, referrer_name, 1)


def release_ref(r: "redis.Redis", ref_key: str, referrer_name: str) -> bool:
    """Release one reference (decrement count).

    Returns True if the referrer was fully removed (count reached zero).
    Uses ``<= 0`` to defensively handle count drift from over-release;
    the entry is removed regardless of how negative the count went.
    """
    remaining = r.hincrby(ref_key, referrer_name, -1)
    if remaining <= 0:
        r.hdel(ref_key, referrer_name)
        return True
    return False


def remove_referrer(r: "redis.Redis", ref_key: str, referrer_name: str) -> None:
    """Remove referrer entirely regardless of count (for teardown/GC)."""
    r.hdel(ref_key, referrer_name)


def get_referrers(r: "redis.Redis", ref_key: str) -> set[str]:
    """Return the set of referrer names."""
    return set(r.hkeys(ref_key))


def has_referrers(r: "redis.Redis", ref_key: str) -> bool:
    """Return True if any referrers exist."""
    return r.hlen(ref_key) > 0
