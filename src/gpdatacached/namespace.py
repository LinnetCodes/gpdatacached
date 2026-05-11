"""GPDC namespace: a MutableMapping-compatible logical isolation space for data objects."""

from __future__ import annotations

import datetime
from collections.abc import MutableMapping
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from .config import GPDCNamespaceConfig
    from .domain import GPDCDomain

from .codec import GPDCScalarCodec, ListCodec
from .keys import (
    make_domain_namespaces_key,
    make_namespace_meta_key,
    make_object_key,
    make_object_key_prefix,
    make_raw_key,
    make_ref_key,
    validate_namespace_name,
    validate_object_name,
)
from .container import TYPE_CLASS_MAP, GPDCContainerObject
from .scalar import GPDCScalarObject

_MISSING = object()


class GPDCNamespace(MutableMapping):
    """Logical isolation space within a GPDCDomain.

    Implements the full ``MutableMapping`` interface backed by Redis.
    Keys are named object names; ``__getitem__`` returns Python native values.
    Use ``get_object()`` to retrieve ``GPDCScalarObject`` instances.
    Anonymous objects (names starting with ``~``) are stored in the same
    Redis key space but are excluded from all public MutableMapping methods
    (``__iter__``, ``__len__``, ``__contains__``).
    """

    def __init__(
        self,
        name: str,
        domain: "GPDCDomain",
        *,
        config: "GPDCNamespaceConfig | None" = None,
    ) -> None:
        validate_namespace_name(name)
        self._name = name
        self._domain = domain
        self._ns_config = config
        self._init_namespace_meta()

    def _init_namespace_meta(self) -> None:
        """Write namespace metadata to Redis if not present.

        Config priority for new namespaces (metadata not yet in Redis):
          1. Fields explicitly set in the ``config`` argument passed to ``__init__``
          2. Domain-level defaults

        If metadata already exists in Redis it is left untouched; only the
        namespace-set registration (``sadd``) is repeated (idempotent).
        """
        meta_key = make_namespace_meta_key(self._domain.name, self._name)
        existing = self._domain.redis.hgetall(meta_key)
        if not existing:
            ns_cfg = self._ns_config
            if ns_cfg is not None and ns_cfg.default_cache_mode is not None:
                cache_mode = ns_cfg.default_cache_mode
            else:
                cache_mode = self._domain.get_default_cache_mode()

            if ns_cfg is not None and ns_cfg.default_ttl_seconds is not None:
                ttl = ns_cfg.default_ttl_seconds
            else:
                ttl = self._domain.get_default_ttl_seconds()

            self._domain.redis.hset(
                meta_key,
                mapping={
                    "default_cache_mode": cache_mode,
                    "default_ttl_seconds": str(ttl),
                },
            )
        # Always register this namespace in the domain's namespace set
        self._domain.redis.sadd(make_domain_namespaces_key(self._domain.name), self._name)

    @property
    def name(self) -> str:
        return self._name

    @property
    def domain(self) -> "GPDCDomain":
        return self._domain

    def _redis(self):
        return self._domain.redis

    def _get_default_cache_mode(self) -> str:
        meta_key = make_namespace_meta_key(self._domain.name, self._name)
        val = self._redis().hget(meta_key, "default_cache_mode")
        return val or self._domain.get_default_cache_mode()

    def _get_default_ttl_seconds(self) -> int:
        meta_key = make_namespace_meta_key(self._domain.name, self._name)
        raw = self._redis().hget(meta_key, "default_ttl_seconds")
        return int(raw) if raw else self._domain.get_default_ttl_seconds()

    # ── Dict-like interface ──────────────────────────────────────────────────

    def _owner_name_from_raw_key(self, raw_key: str) -> str:
        """Extract object name from a raw key like gpdc:{domain}:{ns}:raw:{name}."""
        prefix = f"gpdc:{self._domain.name}:{self._name}:raw:"
        return raw_key[len(prefix) :]

    def _cleanup_old_container_ref(self, key: str, existing_meta: dict) -> None:
        """If key previously referenced another container, remove key from that ref set."""
        old_type = existing_meta.get("type", "")
        if not old_type.startswith("collection:"):
            return
        old_raw_key = existing_meta.get("value", "")
        if not old_raw_key:
            return
        owner_name = self._owner_name_from_raw_key(old_raw_key)
        if owner_name != key:
            # key was referencing another object's raw data
            ref_key = make_ref_key(self._domain.name, self._name, owner_name)
            self._redis().srem(ref_key, key)

    def __setitem__(self, key: str, value: object) -> None:
        validate_object_name(key)

        r = self._redis()
        obj_key = make_object_key(self._domain.name, self._name, key)
        now = datetime.datetime.now(datetime.UTC).isoformat()

        # Case B: assigning a ListObject (reference sharing)
        if isinstance(value, GPDCContainerObject):
            if value._namespace is not self:
                raise ValueError("Cross-namespace container reference sharing is not supported")
            existing = r.hgetall(obj_key)
            if existing:
                self._cleanup_old_container_ref(key, existing)

            src_obj_key = make_object_key(self._domain.name, value._namespace.name, value._name)
            src_meta = r.hgetall(src_obj_key)
            # raw_key is stored in the "value" field of the source's obj: metadata
            shared_raw_key = src_meta.get("value", "")
            # Determine owner name from raw_key (may be src's name or another object's name)
            owner_name = self._owner_name_from_raw_key(shared_raw_key)

            cache_mode = src_meta.get("cache_mode", self._get_default_cache_mode())
            ttl_str = src_meta.get("ttl", "")
            ttl = int(ttl_str) if ttl_str else self._get_default_ttl_seconds()

            # Create obj: entry for key, pointing at same raw key
            r.hset(
                obj_key,
                mapping={
                    "type": src_meta.get("type", "collection:list"),
                    "codec": "native",
                    "value": shared_raw_key,
                    "cache_mode": cache_mode,
                    "ttl": str(ttl) if ttl else "",
                    "created_at": now,
                    "updated_at": now,
                },
            )
            # Add key to the owner's ref: set
            ref_owner_key = make_ref_key(self._domain.name, self._name, owner_name)
            r.sadd(ref_owner_key, key)
            return

        # Case A: assigning a Python list → create new ListObject
        if isinstance(value, list):
            existing = r.hgetall(obj_key)
            if existing:
                self._cleanup_old_container_ref(key, existing)

            raw_key = make_raw_key(self._domain.name, self._name, key)
            cache_mode = self._get_default_cache_mode()
            ttl = self._get_default_ttl_seconds()

            r.hset(
                obj_key,
                mapping={
                    "type": "collection:list",
                    "codec": "native",
                    "value": raw_key,
                    "cache_mode": cache_mode,
                    "ttl": str(ttl) if ttl else "",
                    "created_at": now,
                    "updated_at": now,
                },
            )
            ListCodec.create_raw(r, raw_key, value)
            # Spec §9.3 step 5: "创建空的 ref: set".  Redis returns an empty set
            # for smembers() on a non-existent key, which is functionally
            # equivalent to an explicit empty set.  No physical key creation is
            # needed; the invariant (referrers() == set()) is satisfied by Redis
            # semantics for missing keys.
            if cache_mode in ("ttl", "sliding") and ttl > 0:
                r.expire(obj_key, ttl)
            return

        # Case C: scalar assignment
        # Unwrap GPDCScalarObject → Python value (copy semantics)
        if isinstance(value, GPDCScalarObject):
            value = value.value

        gpdc_type = GPDCScalarCodec.gpdc_type_of(value)
        encoded = GPDCScalarCodec.encode_value(value)

        existing = r.hgetall(obj_key)
        if existing:
            # Update existing object: preserve cache_mode/ttl, update value/type/updated_at
            cache_mode = existing.get("cache_mode", "permanent")
            ttl_raw = existing.get("ttl", "0")
            ttl = int(ttl_raw) if ttl_raw else 0
            pipe = r.pipeline()
            pipe.hset(obj_key, mapping={"type": gpdc_type, "value": encoded, "updated_at": now})
            if cache_mode == "sliding" and ttl > 0:
                pipe.expire(obj_key, ttl)
            pipe.execute()
        else:
            # Create new object
            cache_mode = self._get_default_cache_mode()
            ttl = self._get_default_ttl_seconds()
            pipe = r.pipeline()
            pipe.hset(
                obj_key,
                mapping={
                    "type": gpdc_type,
                    "codec": "native",
                    "value": encoded,
                    "cache_mode": cache_mode,
                    "ttl": str(ttl) if ttl else "",
                    "created_at": now,
                    "updated_at": now,
                },
            )
            if cache_mode in ("ttl", "sliding") and ttl > 0:
                pipe.expire(obj_key, ttl)
            pipe.execute()

    def _get_object(self, key: str):
        validate_object_name(key)
        obj_key = make_object_key(self._domain.name, self._name, key)
        r = self._redis()
        meta = r.hgetall(obj_key)
        if not meta:
            raise KeyError(f"{key!r} not found in namespace {self._name!r}")
        type_str = meta.get("type")
        if type_str is None:
            raise KeyError(f"{key!r} not found in namespace {self._name!r}")
        cls = TYPE_CLASS_MAP.get(type_str)
        if cls is None:
            raise ValueError(f"Unknown GPDC type: {type_str!r}")
        obj = cls(key, self)
        # For container objects, override raw_key with the stored value (supports reference sharing)
        if isinstance(obj, GPDCContainerObject):
            stored_raw_key = meta.get("value", "")
            if stored_raw_key:
                obj.raw_key = stored_raw_key
        return obj

    def __getitem__(self, key: str):
        obj = self._get_object(key)
        if isinstance(obj, GPDCContainerObject):
            return obj
        return obj.value

    def get_object(self, key: str, default=_MISSING):
        try:
            return self._get_object(key)
        except KeyError:
            if default is _MISSING:
                raise
            self[key] = default
            return self._get_object(key)

    def __delitem__(self, key: str) -> None:
        validate_object_name(key)
        r = self._redis()
        obj_key = make_object_key(self._domain.name, self._name, key)
        meta = r.hgetall(obj_key)
        if not meta:
            raise KeyError(f"{key!r} not found in namespace {self._name!r}")

        obj_type = meta.get("type", "")

        if not obj_type.startswith("collection:"):
            # Scalar: just delete the obj: key
            r.delete(obj_key)
            return

        # Container deletion
        stored_raw_key = meta.get("value", "")
        owner_name = self._owner_name_from_raw_key(stored_raw_key) if stored_raw_key else key
        ref_key_of_this = make_ref_key(self._domain.name, self._name, key)

        # Step 1: If this object is a referrer (not the owner), remove from owner's ref set
        if owner_name != key and stored_raw_key:
            ref_owner_key = make_ref_key(self._domain.name, self._name, owner_name)
            r.srem(ref_owner_key, key)

        # Step 2: Delete this object's obj: key
        r.delete(obj_key)

        # Step 3: Only clean up raw: and ref: if this object OWNS the raw data
        if owner_name == key:
            # Check if anyone else references this raw data
            referrers = r.smembers(ref_key_of_this)
            if not referrers:
                # No one references it — safe to delete raw and ref
                if stored_raw_key:
                    r.delete(stored_raw_key)
                r.delete(ref_key_of_this)
            # If referrers exist, leave raw: and ref: intact (referrers still need it)
        else:
            # This was a referrer, not an owner — just delete its own ref: key
            r.delete(ref_key_of_this)

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        try:
            validate_object_name(key)
        except ValueError:
            return False
        obj_key = make_object_key(self._domain.name, self._name, key)
        return self._redis().exists(obj_key) > 0

    def __iter__(self) -> Iterator[str]:
        """Iterate over named object names using SCAN (no KEYS).

        Anonymous objects (names starting with ``~``) are excluded.
        """
        pattern = make_object_key(self._domain.name, self._name, "*")
        prefix = make_object_key_prefix(self._domain.name, self._name)
        cursor = 0
        while True:
            cursor, keys = self._redis().scan(cursor=cursor, match=pattern, count=100)
            for k in keys:
                name = k[len(prefix) :]
                if not name.startswith("~"):
                    yield name
            if cursor == 0:
                break

    def __len__(self) -> int:
        """Return the number of named objects in this namespace.

        Anonymous objects (names starting with ``~``) are not counted.
        O(n) — iterates via SCAN.
        """
        return sum(1 for _ in self)

    def clear(self) -> None:
        """Delete all objects in this namespace, including anonymous objects.

        Uses SCAN + DELETE for efficiency instead of the MutableMapping default
        of repeated ``popitem()`` calls. The namespace ``__meta__`` key is
        preserved so the namespace remains registered and its configuration
        intact after the operation.
        """
        meta_key = make_namespace_meta_key(self._domain.name, self._name)
        pattern = f"gpdc:{self._domain.name}:{self._name}:*"
        r = self._redis()
        cursor = 0
        while True:
            cursor, keys = r.scan(cursor=cursor, match=pattern, count=100)
            to_delete = [k for k in keys if k != meta_key]
            if to_delete:
                r.delete(*to_delete)
            if cursor == 0:
                break
