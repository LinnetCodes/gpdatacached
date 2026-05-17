"""GPDC namespace: a MutableMapping-compatible logical isolation space for data objects."""

from __future__ import annotations

import datetime
from collections.abc import MutableMapping
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from .config import GPDCNamespaceConfig
    from .domain import GPDCDomain

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
from .types import GPDCTypeRegistry

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
            ref_key = make_ref_key(self._domain.name, self._name, owner_name)
            self._redis().srem(ref_key, key)

    def __setitem__(self, key: str, value: object) -> None:
        validate_object_name(key)
        from .container import GPDCContainerObject
        from .scalar import GPDCScalarObject
        from .codec import GPDCContainerCodec, GPDCScalarCodec

        r = self._redis()
        obj_key = make_object_key(self._domain.name, self._name, key)
        existing = r.hgetall(obj_key)

        if existing:
            self._cleanup_old_container_ref(key, existing)

        if isinstance(value, GPDCContainerObject):
            if value._namespace is not self:
                raise ValueError("Cross-namespace container reference sharing is not supported")
            src_obj_key = make_object_key(self._domain.name, value._namespace.name, value._name)
            src_meta = r.hgetall(src_obj_key)
            shared_raw_key = src_meta.get("value", "")
            owner_name = self._owner_name_from_raw_key(shared_raw_key)

            now = datetime.datetime.now(datetime.UTC).isoformat()
            cache_mode = src_meta.get("cache_mode", self._get_default_cache_mode())
            ttl_str = src_meta.get("ttl", "")
            ttl = int(ttl_str) if ttl_str else self._get_default_ttl_seconds()

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
            ref_owner_key = make_ref_key(self._domain.name, self._name, owner_name)
            r.sadd(ref_owner_key, key)
            return

        if isinstance(value, GPDCScalarObject):
            value = value.value

        entry = GPDCTypeRegistry.lookup_by_python_type(value)
        now = datetime.datetime.now(datetime.UTC).isoformat()

        if existing:
            cache_mode = existing.get("cache_mode", "permanent")
            ttl_raw = existing.get("ttl", "0")
            ttl = int(ttl_raw) if ttl_raw else 0

            if entry.is_container:
                raw_key = make_raw_key(self._domain.name, self._name, key)
                r.hset(
                    obj_key,
                    mapping={
                        "type": entry.gpdc_type,
                        "value": raw_key,
                        "updated_at": now,
                    },
                )
                if entry.codec_class and issubclass(entry.codec_class, GPDCContainerCodec):
                    entry.codec_class.delete_raw(self, raw_key)
                    entry.codec_class.create_raw(self, raw_key, value, key, cache_mode, ttl)
            else:
                encoded = GPDCScalarCodec.encode_value(value)
                r.hset(
                    obj_key,
                    mapping={
                        "type": entry.gpdc_type,
                        "value": encoded,
                        "updated_at": now,
                    },
                )
                if cache_mode == "sliding" and ttl > 0:
                    r.expire(obj_key, ttl)
        else:
            cache_mode = self._get_default_cache_mode()
            ttl = self._get_default_ttl_seconds()

            if entry.is_container:
                raw_key = make_raw_key(self._domain.name, self._name, key)
                r.hset(
                    obj_key,
                    mapping={
                        "type": entry.gpdc_type,
                        "codec": "native",
                        "value": raw_key,
                        "cache_mode": cache_mode,
                        "ttl": str(ttl) if ttl else "",
                        "created_at": now,
                        "updated_at": now,
                    },
                )
                if entry.codec_class and issubclass(entry.codec_class, GPDCContainerCodec):
                    entry.codec_class.create_raw(self, raw_key, value, key, cache_mode, ttl)
                if cache_mode in ("ttl", "sliding") and ttl > 0:
                    r.expire(obj_key, ttl)
            else:
                encoded = GPDCScalarCodec.encode_value(value)
                r.hset(
                    obj_key,
                    mapping={
                        "type": entry.gpdc_type,
                        "codec": "native",
                        "value": encoded,
                        "cache_mode": cache_mode,
                        "ttl": str(ttl) if ttl else "",
                        "created_at": now,
                        "updated_at": now,
                    },
                )
                if cache_mode in ("ttl", "sliding") and ttl > 0:
                    r.expire(obj_key, ttl)

    def _get_object(self, key: str):
        validate_object_name(key)
        from .container import GPDCContainerObject

        obj_key = make_object_key(self._domain.name, self._name, key)
        r = self._redis()
        meta = r.hgetall(obj_key)
        if not meta:
            raise KeyError(f"{key!r} not found in namespace {self._name!r}")
        type_str = meta.get("type")
        if type_str is None:
            raise KeyError(f"{key!r} not found in namespace {self._name!r}")
        entry = GPDCTypeRegistry.lookup_by_gpdc_type(type_str)
        obj = entry.object_class(key, self)
        if isinstance(obj, GPDCContainerObject):
            stored_raw_key = meta.get("value", "")
            if stored_raw_key:
                obj.raw_key = stored_raw_key
        return obj

    def __getitem__(self, key: str):
        from .container import GPDCContainerObject

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
            r.delete(obj_key)
            return

        stored_raw_key = meta.get("value", "")
        owner_name = self._owner_name_from_raw_key(stored_raw_key) if stored_raw_key else key
        ref_key_of_this = make_ref_key(self._domain.name, self._name, key)

        if owner_name != key and stored_raw_key:
            ref_owner_key = make_ref_key(self._domain.name, self._name, owner_name)
            r.srem(ref_owner_key, key)

        if owner_name == key and stored_raw_key:
            referrers = r.smembers(ref_key_of_this)
            anon_names = self._collect_anon_refs(stored_raw_key, obj_type)
            for anon_name in anon_names:
                anon_ref_key = make_ref_key(self._domain.name, self._name, anon_name)
                r.srem(anon_ref_key, key)
                if referrers:
                    r.sadd(anon_ref_key, *referrers)
                self._cascade_delete_anonymous(anon_name)

        r.delete(obj_key)

        if owner_name == key:
            referrers = r.smembers(ref_key_of_this)
            if not referrers:
                if stored_raw_key:
                    r.delete(stored_raw_key)
                r.delete(ref_key_of_this)
        else:
            r.delete(ref_key_of_this)
            if stored_raw_key:
                ref_owner_key = make_ref_key(self._domain.name, self._name, owner_name)
                owner_obj_key = make_object_key(self._domain.name, self._name, owner_name)
                if not r.exists(owner_obj_key) and not r.exists(ref_owner_key):
                    r.delete(stored_raw_key)

    def _collect_anon_refs(self, raw_key: str, raw_type: str) -> list[str]:
        r = self._redis()
        anon_names = []
        if raw_type == "collection:dict":
            for field, val in r.hgetall(raw_key).items():
                if isinstance(val, str) and val.startswith("ref:"):
                    ref_obj_key = val[len("ref:") :]
                    anon_name = ref_obj_key.split(":obj:")[1]
                    anon_names.append(anon_name)
        elif raw_type == "collection:list":
            for val in r.lrange(raw_key, 0, -1):
                if isinstance(val, str) and val.startswith("ref:"):
                    ref_obj_key = val[len("ref:") :]
                    anon_name = ref_obj_key.split(":obj:")[1]
                    anon_names.append(anon_name)
        elif raw_type == "collection:set":
            for val in r.smembers(raw_key):
                if isinstance(val, str) and val.startswith("ref:"):
                    ref_obj_key = val[len("ref:") :]
                    anon_name = ref_obj_key.split(":obj:")[1]
                    anon_names.append(anon_name)
        return anon_names

    def _cascade_delete_anonymous(self, anon_name: str) -> None:
        r = self._redis()
        obj_key = make_object_key(self._domain.name, self._name, anon_name)
        meta = r.hgetall(obj_key)
        if not meta:
            return

        obj_type = meta.get("type", "")
        raw_key = meta.get("value", "")
        ref_key = make_ref_key(self._domain.name, self._name, anon_name)

        referrers = r.smembers(ref_key)
        if referrers:
            return

        if raw_key and obj_type.startswith("collection:"):
            nested_anons = self._collect_anon_refs(raw_key, obj_type)
            r.delete(raw_key)

            for nested in nested_anons:
                nested_ref_key = make_ref_key(self._domain.name, self._name, nested)
                r.srem(nested_ref_key, anon_name)
                self._cascade_delete_anonymous(nested)

        r.delete(ref_key)
        r.delete(obj_key)

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
