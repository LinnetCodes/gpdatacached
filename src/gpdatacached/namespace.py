"""GPDC namespace: a MutableMapping-compatible logical isolation space for data objects."""

from __future__ import annotations

import datetime
from collections.abc import MutableMapping
from typing import TYPE_CHECKING, NamedTuple, Iterator

if TYPE_CHECKING:
    from .config import GPDCNamespaceConfig
    from .domain import GPDCDomain

from .keys import (
    collect_ref_names_from_raw,
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
from .refset import add_referrer, release_ref, remove_referrer, get_referrers, has_referrers
from .types import GPDCTypeRegistry


class BindingResolution(NamedTuple):
    binding_name: str
    canonical_name: str
    binding_kind: str
    binding_obj_key: str
    canonical_obj_key: str
    canonical_raw_key: str
    meta: ObjectMeta


class GPDCNamespace(MutableMapping):
    """Logical isolation space within a GPDCDomain.

    Implements the full ``MutableMapping`` interface backed by Redis.
    Keys are named object names; ``__getitem__`` returns Python native values.
    Use ``get_object()`` to retrieve ``GPDCDataObject`` instances (read-only).
    Use ``ensure_object(key, default)`` to get an object or create it with a default value.
    Anonymous objects (names starting with ``~``) are stored in the same
    Redis key space but are excluded from all public MutableMapping methods
    (``__iter__``, ``__len__``, ``__contains__``).

    **Design constraint:** Cross-namespace container references are not
    supported.  All ref bindings point to objects within the same namespace.
    This allows ``clear()`` to safely delete all keys in bulk without
    per-object referrer cleanup.
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

    def __repr__(self) -> str:
        return f"GPDCNamespace({self._name!r}, domain={self._domain.name!r})"

    def _get_default_cache_mode(self) -> str:
        meta_key = make_namespace_meta_key(self._domain.name, self._name)
        val = self._redis().hget(meta_key, "default_cache_mode")
        return val or self._domain.get_default_cache_mode()

    def _get_default_ttl_seconds(self) -> int:
        meta_key = make_namespace_meta_key(self._domain.name, self._name)
        raw = self._redis().hget(meta_key, "default_ttl_seconds")
        return int(raw) if raw else self._domain.get_default_ttl_seconds()

    # ── Dict-like interface ──────────────────────────────────────────────────

    def _resolve_binding(self, key: str) -> BindingResolution | None:
        """Resolve a namespace binding key to its canonical owner."""
        r = self._redis()
        obj_key = make_object_key(self._domain.name, self._name, key)
        key_type = r.type(obj_key)

        if key_type == "hash":
            meta = ObjectMeta.load(r, obj_key)
            if meta is None:
                return None
            raw_key = meta.value if meta.is_collection() else ""
            return BindingResolution(
                binding_name=key,
                canonical_name=key,
                binding_kind="owner",
                binding_obj_key=obj_key,
                canonical_obj_key=obj_key,
                canonical_raw_key=raw_key,
                meta=meta,
            )
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
            return BindingResolution(
                binding_name=key,
                canonical_name=canonical_name,
                binding_kind="reference",
                binding_obj_key=obj_key,
                canonical_obj_key=canonical_obj_key,
                canonical_raw_key=raw_key,
                meta=canonical_meta,
            )
        return None

    def _cleanup_alias_binding(self, key: str, existing: BindingResolution) -> None:
        """Remove an alias string binding and clean up its ref set entry."""
        r = self._redis()
        canonical_name = existing.canonical_name
        ref_key = make_ref_key(self._domain.name, self._name, canonical_name)
        release_ref(r, ref_key, key)
        r.delete(existing.binding_obj_key)
        if canonical_name.startswith("~") and not has_referrers(r, ref_key):
            self._cascade_delete_anonymous(canonical_name)

    def _cleanup_existing_for_overwrite(self, key: str, existing: BindingResolution) -> None:
        """Clean up an existing binding before it is overwritten with a container alias."""
        from .exceptions import CanonicalObjectReferencedError

        r = self._redis()
        if existing.binding_kind == "reference":
            self._cleanup_alias_binding(key, existing)
        elif existing.binding_kind == "owner" and existing.meta.type.startswith("collection:"):
            ref_key = make_ref_key(self._domain.name, self._name, key)
            referrers = get_referrers(r, ref_key)
            if referrers:
                raise CanonicalObjectReferencedError(
                    canonical_name=key,
                    canonical_uri=make_uri(self._domain.name, self._name, key),
                    referrers=sorted(referrers),
                )
            self._teardown_owner_container(key, existing.meta)
            r.delete(existing.binding_obj_key)
        else:
            r.delete(existing.binding_obj_key)

    def _teardown_owner_container(self, key: str, meta: ObjectMeta) -> None:
        """Remove an owner container's raw data, anonymous children, and ref sets."""
        r = self._redis()
        stored_raw_key = meta.value
        obj_type = meta.type
        ref_key = make_ref_key(self._domain.name, self._name, key)

        if stored_raw_key and obj_type.startswith("collection:"):
            nested_ref_names = collect_ref_names_from_raw(r, stored_raw_key, obj_type)
            for nested_name in nested_ref_names:
                nested_ref_key = make_ref_key(self._domain.name, self._name, nested_name)
                remove_referrer(r, nested_ref_key, key)
                if nested_name.startswith("~"):
                    self._cascade_delete_anonymous(nested_name)

        if stored_raw_key:
            r.delete(stored_raw_key)
        r.delete(ref_key)

    def _write_scalar_meta(
        self,
        obj_key: str,
        entry,
        encoded_value: str,
        policy,
        now: datetime.datetime,
        *,
        is_new: bool,
        obj_name: str = "",
    ) -> None:
        from .lifecycle import apply_ttl

        r = self._redis()
        meta = ObjectMeta(
            type=entry.gpdc_type,
            value=encoded_value,
            cache_mode=policy.cache_mode,
            ttl=policy.declared_ttl,
            owner=obj_name,
            created_at=now if is_new else None,
            updated_at=now,
        )
        meta.save(r, obj_key)
        apply_ttl(r, [obj_key], policy)

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
        obj_name: str = "",
    ) -> None:
        r = self._redis()
        meta = ObjectMeta(
            type=entry.gpdc_type,
            value=raw_key,
            cache_mode=policy.cache_mode,
            ttl=policy.declared_ttl,
            owner=obj_name,
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
        add_referrer(r, ref_key, key)

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
            if existing and existing.canonical_name == value.canonical_name:
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
            if existing.binding_kind == "owner" and existing.meta.type.startswith("collection:"):
                ref_key = make_ref_key(self._domain.name, self._name, key)
                referrers = get_referrers(r, ref_key)
                if referrers:
                    raise CanonicalObjectReferencedError(
                        canonical_name=key,
                        canonical_uri=make_uri(self._domain.name, self._name, key),
                        referrers=sorted(referrers),
                    )
                self._teardown_owner_container(key, existing.meta)
                r.delete(obj_key)
            elif existing.binding_kind == "reference":
                self._cleanup_alias_binding(key, existing)
            elif existing.binding_kind == "owner" and not entry.is_container:
                # Design intent (docs/lifecycle.md "Overwrite caveat"): only
                # scalar-overwrites-scalar preserves the previous policy. When the
                # new value is a container, fall through to the default-policy path
                # below — the scalar owner binding is torn down and re-created like
                # any container overwrite.
                is_new = False
                from .lifecycle import policy_from_meta

                policy = policy_from_meta(existing.meta)

        if policy is None:
            from .lifecycle import policy_from_defaults

            policy = policy_from_defaults(self._get_default_cache_mode(), self._get_default_ttl_seconds())

        if entry.is_container:
            raw_key = make_raw_key(self._domain.name, self._name, key)
            try:
                count = len(value)
            except TypeError:
                model_fields = getattr(type(value), "model_fields", None)
                if model_fields is not None:
                    count = len(model_fields)
                else:
                    count = 0
            self._write_container_meta(
                obj_key, entry, raw_key, policy, now, is_new=is_new, item_count=count, obj_name=key
            )
            try:
                if entry.codec_class and issubclass(entry.codec_class, GPDCContainerCodec):
                    if not is_new:
                        entry.codec_class.delete_raw(self, raw_key)
                    from .types import encode_session

                    with encode_session(self):
                        entry.codec_class.create_raw(self, raw_key, value, key, policy.cache_mode, policy.declared_ttl)
            except BaseException:
                # A failed encode must not leave the obj: meta (written above,
                # before the encode session) as an orphan key. create_raw writes
                # raw: only after a successful encode, so raw:/ref: are typically
                # absent here, but delete them defensively (CQR-2026-07-24 logic.01).
                ref_key = make_ref_key(self._domain.name, self._name, key)
                r.delete(obj_key, raw_key, ref_key)
                raise
            from .lifecycle import apply_ttl

            ref_key = make_ref_key(self._domain.name, self._name, key)
            apply_ttl(r, [obj_key, raw_key, ref_key], policy)
        else:
            codec = entry.codec_class
            encoded = codec.encode_value(value)
            self._write_scalar_meta(obj_key, entry, encoded, policy, now, is_new=is_new, obj_name=key)

    def _get_object(self, key: str):
        validate_object_name(key)
        from .container import GPDCContainerObject

        resolution = self._resolve_binding(key)
        if not resolution:
            raise KeyError(f"{key!r} not found in namespace {self._name!r}")

        type_str = resolution.meta.type
        entry = GPDCTypeRegistry.lookup_by_gpdc_type(type_str)

        canonical_name = resolution.canonical_name
        is_ref = resolution.binding_kind == "reference"

        obj = entry.object_class(key, self, canonical_name=canonical_name if is_ref else None)

        if isinstance(obj, GPDCContainerObject):
            stored_raw_key = resolution.canonical_raw_key
            if stored_raw_key:
                obj.raw_key = stored_raw_key
            if resolution.binding_kind == "reference":
                canonical_ref_key = make_ref_key(self._domain.name, self._name, resolution.canonical_name)
                obj.ref_key = canonical_ref_key

        return obj

    def __getitem__(self, key: str):
        from .container import GPDCContainerObject

        # by design: 标量返回原生值（ns["count"] → 42），容器返回对象本身。
        # 容器必须返回对象，否则用户无法调用 append/pop 等方法操作 Redis。
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

        obj_key = resolution.binding_obj_key

        if resolution.binding_kind == "reference":
            canonical_name = resolution.canonical_name
            ref_key = make_ref_key(self._domain.name, self._name, canonical_name)
            release_ref(r, ref_key, key)
            r.delete(obj_key)
            if canonical_name.startswith("~") and not has_referrers(r, ref_key):
                self._cascade_delete_anonymous(canonical_name)
            return

        obj_type = resolution.meta.type

        if not obj_type.startswith("collection:"):
            r.delete(obj_key)
            return

        ref_key = make_ref_key(self._domain.name, self._name, key)
        referrers = get_referrers(r, ref_key)
        if referrers:
            raise CanonicalObjectReferencedError(
                canonical_name=key,
                canonical_uri=make_uri(self._domain.name, self._name, key),
                referrers=sorted(referrers),
            )

        self._teardown_owner_container(key, resolution.meta)
        r.delete(obj_key)

    def _cascade_delete_anonymous(self, anon_name: str) -> None:
        r = self._redis()
        obj_key = make_object_key(self._domain.name, self._name, anon_name)
        meta = ObjectMeta.load(r, obj_key)
        if meta is None:
            return

        raw_key = meta.value
        ref_key = make_ref_key(self._domain.name, self._name, anon_name)

        referrers = get_referrers(r, ref_key)
        if referrers:
            return

        if raw_key and meta.is_collection():
            nested_ref_names = collect_ref_names_from_raw(r, raw_key, meta.type)
            r.delete(raw_key)

            for nested in nested_ref_names:
                nested_ref_key = make_ref_key(self._domain.name, self._name, nested)
                remove_referrer(r, nested_ref_key, anon_name)
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

        Uses Redis pipeline to batch TYPE / GET commands per SCAN page,
        reducing round trips from O(N) to ~3 per page.
        """
        pattern = make_object_key(self._domain.name, self._name, "*")
        prefix = make_object_key_prefix(self._domain.name, self._name)
        obj_prefix = make_object_key_prefix(self._domain.name, self._name)
        r = self._redis()
        cursor = 0
        while True:
            cursor, keys = r.scan(cursor=cursor, match=pattern, count=100)
            named = [(k, k[len(prefix) :]) for k in keys if not k[len(prefix) :].startswith("~")]
            if not named:
                if cursor == 0:
                    break
                continue

            # Batch TYPE for all candidate keys in this page
            pipe = r.pipeline(transaction=False)
            for k, _ in named:
                pipe.type(k)
            key_types = pipe.execute()

            # Owner bindings (hash): yield directly — SCAN confirmed existence
            ref_keys = []
            for (k, name), kt in zip(named, key_types):
                if kt == "hash":
                    yield name
                elif kt == "string":
                    ref_keys.append((k, name))

            # Ref aliases (string): batch GET to find canonical key
            if ref_keys:
                pipe = r.pipeline(transaction=False)
                for k, _ in ref_keys:
                    pipe.get(k)
                ref_values = pipe.execute()

                canonical_pairs = []
                for (k, name), v in zip(ref_keys, ref_values):
                    if v and v.startswith("ref:"):
                        canonical = v[len("ref:") :]
                        if canonical.startswith(obj_prefix):
                            canonical_pairs.append((name, canonical))

                # Batch TYPE for canonical keys
                if canonical_pairs:
                    pipe = r.pipeline(transaction=False)
                    for _, ck in canonical_pairs:
                        pipe.type(ck)
                    canonical_types = pipe.execute()

                    for (name, _), ct in zip(canonical_pairs, canonical_types):
                        if ct == "hash":
                            yield name

            if cursor == 0:
                break

    def __len__(self) -> int:
        """Return the number of named objects in this namespace.

        Anonymous objects (names starting with ``~``) are not counted.
        O(n) — iterates via SCAN.
        """
        # by design: _ discards object names; sum counts via __iter__.
        return sum(1 for _ in self)

    def clear(self) -> None:
        """Delete all objects in this namespace, including anonymous objects.

        Uses SCAN + DELETE for efficiency instead of the MutableMapping default
        of repeated ``popitem()`` calls. The namespace ``__meta__`` key is
        preserved so the namespace remains registered and its configuration
        intact after the operation.

        Because cross-namespace references are not supported (see class
        docstring), it is safe to delete all keys in bulk without walking
        the per-object referrer / cascade-delete logic used by
        ``__delitem__``.
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

    def destroy(self) -> None:
        self._domain.delete_namespace(self._name)
