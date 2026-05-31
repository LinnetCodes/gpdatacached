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
    make_uri,
    validate_namespace_name,
    validate_object_name,
)
from .meta import ObjectMeta
from .types import GPDCTypeRegistry


class GPDCNamespace(MutableMapping):
    """Logical isolation space within a GPDCDomain.

    Implements the full ``MutableMapping`` interface backed by Redis.
    Keys are named object names; ``__getitem__`` returns Python native values.
    Use ``get_object()`` to retrieve ``GPDCDataObject`` instances (read-only).
    Use ``ensure_object(key, default)`` to get an object or create it with a default value.
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

    def _resolve_binding(self, key: str) -> dict | None:
        """Resolve a namespace binding key to its canonical owner."""
        r = self._redis()
        obj_key = make_object_key(self._domain.name, self._name, key)
        key_type = r.type(obj_key)

        if key_type == "hash":
            meta = ObjectMeta.load(r, obj_key)
            if meta is None:
                return None
            raw_key = meta.value if meta.is_collection() else ""
            return {
                "binding_name": key,
                "canonical_name": key,
                "binding_kind": "owner",
                "binding_obj_key": obj_key,
                "canonical_obj_key": obj_key,
                "canonical_raw_key": raw_key,
                "type": meta.type,
                "meta": meta,
            }
        elif key_type == "string":
            ref_value = r.get(obj_key)
            if not ref_value or not ref_value.startswith("ref:"):
                return None
            canonical_obj_key = ref_value[len("ref:") :]
            obj_prefix = make_object_key_prefix(self._domain.name, self._name)
            if not canonical_obj_key.startswith(obj_prefix):
                return None
            canonical_key_type = r.type(canonical_obj_key)
            if canonical_key_type != "hash":
                return None
            canonical_name = canonical_obj_key[len(obj_prefix) :]
            canonical_meta = ObjectMeta.load(r, canonical_obj_key)
            if canonical_meta is None:
                return None
            raw_key = canonical_meta.value if canonical_meta.is_collection() else ""
            return {
                "binding_name": key,
                "canonical_name": canonical_name,
                "binding_kind": "reference",
                "binding_obj_key": obj_key,
                "canonical_obj_key": canonical_obj_key,
                "canonical_raw_key": raw_key,
                "type": canonical_meta.type,
                "meta": canonical_meta,
            }
        return None

    def _cleanup_alias_binding(self, key: str, existing: dict) -> None:
        """Remove an alias string binding and clean up its ref set entry."""
        r = self._redis()
        canonical_name = existing["canonical_name"]
        ref_key = make_ref_key(self._domain.name, self._name, canonical_name)
        r.srem(ref_key, key)
        r.delete(existing["binding_obj_key"])
        if canonical_name.startswith("~") and not r.smembers(ref_key):
            self._cascade_delete_anonymous(canonical_name)

    def _cleanup_existing_for_overwrite(self, key: str, existing: dict) -> None:
        """Clean up an existing binding before it is overwritten with a container alias."""
        from .exceptions import CanonicalObjectReferencedError

        r = self._redis()
        if existing["binding_kind"] == "reference":
            self._cleanup_alias_binding(key, existing)
        elif existing["binding_kind"] == "owner" and existing["type"].startswith("collection:"):
            ref_key = make_ref_key(self._domain.name, self._name, key)
            referrers = r.smembers(ref_key)
            if referrers:
                raise CanonicalObjectReferencedError(
                    canonical_name=key,
                    canonical_uri=make_uri(self._domain.name, self._name, key),
                    referrers=sorted(referrers),
                )
            self._teardown_owner_container(key, existing["meta"])
            r.delete(existing["binding_obj_key"])
        else:
            r.delete(existing["binding_obj_key"])

    def _teardown_owner_container(self, key: str, meta: ObjectMeta) -> None:
        """Remove an owner container's raw data, anonymous children, and ref sets."""
        r = self._redis()
        stored_raw_key = meta.value
        obj_type = meta.type
        ref_key = make_ref_key(self._domain.name, self._name, key)

        if stored_raw_key and obj_type.startswith("collection:"):
            nested_ref_names = self._collect_nested_ref_names(stored_raw_key, obj_type)
            for nested_name in nested_ref_names:
                nested_ref_key = make_ref_key(self._domain.name, self._name, nested_name)
                r.srem(nested_ref_key, key)
                if nested_name.startswith("~"):
                    self._cascade_delete_anonymous(nested_name)

        if stored_raw_key:
            r.delete(stored_raw_key)
        r.delete(ref_key)

    def _write_scalar_meta(
        self, obj_key: str, entry, encoded_value: str, policy, now: datetime.datetime, *, is_new: bool
    ) -> None:
        from .meta import apply_lifecycle

        r = self._redis()
        meta = ObjectMeta(
            type=entry.gpdc_type,
            value=encoded_value,
            cache_mode=policy.cache_mode,
            ttl=policy.declared_ttl,
            created_at=now if is_new else None,
            updated_at=now,
        )
        meta.save(r, obj_key)
        apply_lifecycle(r, obj_key, "", "", False, policy)

    def _write_container_meta(
        self,
        obj_key: str,
        entry,
        raw_key: str,
        policy,
        now: datetime.datetime,
        *,
        is_new: bool,
        item_count: int = 0,
    ) -> None:
        r = self._redis()
        meta = ObjectMeta(
            type=entry.gpdc_type,
            value=raw_key,
            cache_mode=policy.cache_mode,
            ttl=policy.declared_ttl,
            created_at=now if is_new else None,
            updated_at=now,
            item_count=item_count,
        )
        meta.save(r, obj_key)

    def _setitem_container_ref(self, key: str, container) -> None:
        from .container import GPDCContainerObject

        assert isinstance(container, GPDCContainerObject)
        if container._namespace is not self:
            raise ValueError("Cross-namespace container reference sharing is not supported")

        r = self._redis()
        canonical_name = container.canonical_name
        canonical_obj_key = make_object_key(self._domain.name, self._name, canonical_name)

        alias_obj_key = make_object_key(self._domain.name, self._name, key)
        r.set(alias_obj_key, f"ref:{canonical_obj_key}")

        ref_key = make_ref_key(self._domain.name, self._name, canonical_name)
        r.sadd(ref_key, key)

    def __setitem__(self, key: str, value: object) -> None:
        validate_object_name(key)
        from .container import GPDCContainerObject
        from .scalar import GPDCScalarObject
        from .codec import GPDCContainerCodec
        from .exceptions import CanonicalObjectReferencedError

        r = self._redis()
        obj_key = make_object_key(self._domain.name, self._name, key)

        existing = self._resolve_binding(key)

        if isinstance(value, GPDCContainerObject):
            if value._namespace is not self:
                raise ValueError("Cross-namespace container reference sharing is not supported")
            if existing and existing["canonical_name"] == value.canonical_name:
                return
            if existing:
                self._cleanup_existing_for_overwrite(key, existing)
            self._setitem_container_ref(key, value)
            return

        if isinstance(value, GPDCScalarObject):
            value = value.value

        entry = GPDCTypeRegistry.lookup_by_python_type(value)
        now = datetime.datetime.now(datetime.UTC)

        is_new = True
        policy = None

        if existing:
            if existing["binding_kind"] == "owner" and existing["type"].startswith("collection:"):
                ref_key = make_ref_key(self._domain.name, self._name, key)
                referrers = r.smembers(ref_key)
                if referrers:
                    raise CanonicalObjectReferencedError(
                        canonical_name=key,
                        canonical_uri=make_uri(self._domain.name, self._name, key),
                        referrers=sorted(referrers),
                    )
                self._teardown_owner_container(key, existing["meta"])
                r.delete(obj_key)
            elif existing["binding_kind"] == "reference":
                self._cleanup_alias_binding(key, existing)
            elif existing["binding_kind"] == "owner":
                is_new = False
                from .ttl import policy_from_meta

                policy = policy_from_meta(existing["meta"])

        if policy is None:
            from .ttl import policy_from_defaults

            policy = policy_from_defaults(self._get_default_cache_mode(), self._get_default_ttl_seconds())

        if entry.is_container:
            raw_key = make_raw_key(self._domain.name, self._name, key)
            self._write_container_meta(obj_key, entry, raw_key, policy, now, is_new=is_new, item_count=len(value))
            if entry.codec_class and issubclass(entry.codec_class, GPDCContainerCodec):
                if not is_new:
                    entry.codec_class.delete_raw(self, raw_key)
                entry.codec_class.create_raw(self, raw_key, value, key, policy.cache_mode, policy.declared_ttl)
            from .meta import apply_lifecycle

            ref_key = make_ref_key(self._domain.name, self._name, key)
            apply_lifecycle(r, obj_key, raw_key, ref_key, True, policy)
        else:
            codec = entry.codec_class
            encoded = codec.encode_value(value)
            self._write_scalar_meta(obj_key, entry, encoded, policy, now, is_new=is_new)

    def _get_object(self, key: str):
        validate_object_name(key)
        from .container import GPDCContainerObject

        resolution = self._resolve_binding(key)
        if not resolution:
            raise KeyError(f"{key!r} not found in namespace {self._name!r}")

        type_str = resolution["type"]
        entry = GPDCTypeRegistry.lookup_by_gpdc_type(type_str)

        canonical_name = resolution["canonical_name"]
        is_ref = resolution["binding_kind"] == "reference"

        obj = entry.object_class(key, self, canonical_name=canonical_name if is_ref else None)

        if isinstance(obj, GPDCContainerObject):
            stored_raw_key = resolution["canonical_raw_key"]
            if stored_raw_key:
                obj.raw_key = stored_raw_key

        return obj

    def __getitem__(self, key: str):
        from .container import GPDCContainerObject

        obj = self._get_object(key)
        if isinstance(obj, GPDCContainerObject):
            return obj
        return obj.value

    def get_object(self, key: str):
        return self._get_object(key)

    def ensure_object(self, key: str, default):
        try:
            return self._get_object(key)
        except KeyError:
            self[key] = default
            return self._get_object(key)

    def __delitem__(self, key: str) -> None:
        validate_object_name(key)
        from .exceptions import CanonicalObjectReferencedError

        r = self._redis()
        resolution = self._resolve_binding(key)
        if not resolution:
            raise KeyError(f"{key!r} not found in namespace {self._name!r}")

        obj_key = resolution["binding_obj_key"]

        if resolution["binding_kind"] == "reference":
            canonical_name = resolution["canonical_name"]
            ref_key = make_ref_key(self._domain.name, self._name, canonical_name)
            r.srem(ref_key, key)
            r.delete(obj_key)
            if canonical_name.startswith("~") and not r.smembers(ref_key):
                self._cascade_delete_anonymous(canonical_name)
            return

        obj_type = resolution["type"]

        if not obj_type.startswith("collection:"):
            r.delete(obj_key)
            return

        ref_key = make_ref_key(self._domain.name, self._name, key)
        referrers = r.smembers(ref_key)
        if referrers:
            raise CanonicalObjectReferencedError(
                canonical_name=key,
                canonical_uri=make_uri(self._domain.name, self._name, key),
                referrers=sorted(referrers),
            )

        self._teardown_owner_container(key, resolution["meta"])
        r.delete(obj_key)

    def _collect_nested_ref_names(self, raw_key: str, raw_type: str) -> list[str]:
        r = self._redis()
        ref_names = []

        def add_ref_name(encoded_ref: str) -> None:
            ref_obj_key = encoded_ref[len("ref:") :]
            ref_names.append(ref_obj_key.split(":obj:")[1])

        if raw_type == "collection:dict":
            for field, val in r.hgetall(raw_key).items():
                if isinstance(val, str) and val.startswith("ref:"):
                    add_ref_name(val)
        elif raw_type == "collection:list":
            for val in r.lrange(raw_key, 0, -1):
                if isinstance(val, str) and val.startswith("ref:"):
                    add_ref_name(val)
        elif raw_type == "collection:set":
            for val in r.smembers(raw_key):
                if isinstance(val, str) and val.startswith("ref:"):
                    add_ref_name(val)
        return ref_names

    def _cascade_delete_anonymous(self, anon_name: str) -> None:
        r = self._redis()
        obj_key = make_object_key(self._domain.name, self._name, anon_name)
        meta = ObjectMeta.load(r, obj_key)
        if meta is None:
            return

        raw_key = meta.value
        ref_key = make_ref_key(self._domain.name, self._name, anon_name)

        referrers = r.smembers(ref_key)
        if referrers:
            return

        if raw_key and meta.is_collection():
            nested_ref_names = self._collect_nested_ref_names(raw_key, meta.type)
            r.delete(raw_key)

            for nested in nested_ref_names:
                nested_ref_key = make_ref_key(self._domain.name, self._name, nested)
                r.srem(nested_ref_key, anon_name)
                if nested.startswith("~"):
                    self._cascade_delete_anonymous(nested)

        r.delete(ref_key)
        r.delete(obj_key)

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        if key.startswith("~"):
            return False
        return self._resolve_binding(key) is not None

    def __iter__(self) -> Iterator[str]:
        """Iterate over named object names using SCAN (no KEYS).

        Anonymous objects (names starting with ``~``) are excluded.
        Stale aliases whose canonical owner no longer exists are excluded.
        """
        pattern = make_object_key(self._domain.name, self._name, "*")
        prefix = make_object_key_prefix(self._domain.name, self._name)
        cursor = 0
        while True:
            cursor, keys = self._redis().scan(cursor=cursor, match=pattern, count=100)
            for k in keys:
                name = k[len(prefix) :]
                if not name.startswith("~"):
                    if self._resolve_binding(name) is not None:
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
