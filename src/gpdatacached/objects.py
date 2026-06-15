from __future__ import annotations

import datetime
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .domain import GPDCDomain
    from .namespace import GPDCNamespace

from .exceptions import LifecyclePermissionError, ObjectGoneError
from .keys import make_object_key, make_uri
from .meta import ObjectMeta
from .lifecycle import _LifecycleContext


class GPDCDataObject(ABC):
    """Abstract base class for all GPDC data objects."""

    def __init__(
        self,
        name: str,
        namespace: "GPDCNamespace",
        *,
        canonical_name: str | None = None,
    ) -> None:
        self._name = name
        self._namespace = namespace
        self._canonical_name = canonical_name if canonical_name is not None else name
        self._is_reference = canonical_name is not None and canonical_name != name

    @property
    def name(self) -> str:
        return self._name

    @property
    def namespace(self) -> "GPDCNamespace":
        return self._namespace

    @property
    def domain(self) -> "GPDCDomain":
        return self._namespace.domain

    @property
    def uri(self) -> str:
        return make_uri(self.domain.name, self._namespace.name, self._name)

    @property
    def is_reference(self) -> bool:
        return self._is_reference

    @property
    def canonical_name(self) -> str:
        return self._canonical_name

    @property
    def canonical_uri(self) -> str:
        return make_uri(self.domain.name, self._namespace.name, self._canonical_name)

    @property
    @abstractmethod
    def type(self) -> str:
        """GPDC type string, e.g., 'scalar:int'."""

    @property
    @abstractmethod
    def value(self) -> object:
        """The Python value of this data object."""

    def _redis_key(self) -> str:
        return make_object_key(self.domain.name, self._namespace.name, self._name)

    def _canonical_redis_key(self) -> str:
        return make_object_key(self.domain.name, self._namespace.name, self._canonical_name)

    def _redis(self):
        return self.domain.redis

    @property
    def meta(self) -> ObjectMeta | None:
        return ObjectMeta.load(self._redis(), self._canonical_redis_key())

    @property
    def cache_mode(self) -> str:
        meta = self.meta
        if meta is None:
            raise ObjectGoneError(self.canonical_uri)
        return meta.cache_mode

    def _load_lifecycle_context(self) -> _LifecycleContext:
        from .keys import make_ref_key

        key = self._canonical_redis_key()
        r = self._redis()
        meta = ObjectMeta.load(r, key)
        if meta is None:
            raise ObjectGoneError(self.canonical_uri)
        is_collection = meta.is_collection()
        raw_key = meta.value if is_collection else ""
        ref_key = make_ref_key(self.domain.name, self.namespace.name, self._canonical_name) if is_collection else ""
        return _LifecycleContext(r=r, key=key, meta=meta, raw_key=raw_key, ref_key=ref_key, is_collection=is_collection)

    def _check_owner_permission(self, meta: ObjectMeta) -> None:
        if meta.owner and meta.owner != self.canonical_name:
            raise LifecyclePermissionError(
                canonical_name=self.canonical_name,
                owner_name=meta.owner,
            )

    def set_lifecycle(self, policy, *, propagate: bool = True) -> None:
        from .lifecycle import (
            policy_from_meta,
            apply_ttl,
            propagate_lifecycle,
        )

        ctx = self._load_lifecycle_context()

        current = policy_from_meta(ctx.meta)
        changed = current.cache_mode != policy.cache_mode or current.declared_ttl != policy.declared_ttl

        updated_meta = ctx.meta.with_updates(
            cache_mode=policy.cache_mode,
            ttl=policy.declared_ttl,
            updated_at=datetime.datetime.now(datetime.UTC),
        )
        updated_meta.save(ctx.r, ctx.key)

        if changed:
            apply_ttl(ctx.r, [ctx.key, ctx.raw_key, ctx.ref_key], policy)

        if changed and propagate:
            propagate_lifecycle(
                ctx.r,
                self.domain.name,
                self.namespace.name,
                self.canonical_name,
                policy,
            )

    def refresh_ttl(self) -> None:
        """Re-apply this object's own TTL to its keys and cascade-refresh its owned subtree.

        Used by the sliding-window refresh path on mutation. No-op for objects
        whose policy declares no expiry (permanent / zero ttl).
        """
        from .lifecycle import apply_ttl, propagate_ttl_refresh, policy_from_meta

        ctx = self._load_lifecycle_context()
        policy = policy_from_meta(ctx.meta)
        if policy.expire_seconds is None:
            return
        keys = [ctx.key]
        if ctx.is_collection:
            keys.append(ctx.raw_key)
            keys.append(ctx.ref_key)
        apply_ttl(ctx.r, keys, policy)
        propagate_ttl_refresh(ctx.r, self.domain.name, self.namespace.name, self.canonical_name)

    def update_cache_mode_and_ttl(self, cache_mode: str, ttl: int) -> None:
        from .lifecycle import policy_from_defaults

        policy = policy_from_defaults(cache_mode, ttl)
        ctx = self._load_lifecycle_context()
        self._check_owner_permission(ctx.meta)
        self.set_lifecycle(policy)

    def take_ownership(self) -> None:
        if not self._is_reference:
            raise TypeError("take_ownership() can only be called on named reference objects")
        if self._name.startswith("~"):
            raise TypeError("take_ownership() can only be called on named reference objects")

        key = self._canonical_redis_key()
        r = self._redis()
        meta = ObjectMeta.load(r, key)
        if meta is None:
            raise ObjectGoneError(self.canonical_uri)

        if meta.owner == self.canonical_name:
            return

        meta.with_updates(owner=self.canonical_name).save(r, key)

    @property
    def ttl(self) -> int:
        meta = self.meta
        if meta is None:
            raise ObjectGoneError(self.canonical_uri)
        return meta.ttl

    @property
    def created_at(self) -> datetime.datetime | None:
        meta = self.meta
        if meta is None:
            raise ObjectGoneError(self.canonical_uri)
        return meta.created_at

    @property
    def updated_at(self) -> datetime.datetime | None:
        meta = self.meta
        if meta is None:
            raise ObjectGoneError(self.canonical_uri)
        return meta.updated_at

    def expire_in(self, seconds: int) -> None:
        from .lifecycle import TTLPolicy

        ctx = self._load_lifecycle_context()
        if ctx.meta.cache_mode != "ttl":
            raise TypeError("expire_in() only applies to objects with cache_mode='ttl'")
        self._check_owner_permission(ctx.meta)
        policy = TTLPolicy(cache_mode="ttl", declared_ttl=seconds)
        self.set_lifecycle(policy)

    def expire_at(self, when: datetime.datetime) -> None:
        from .lifecycle import TTLPolicy

        ctx = self._load_lifecycle_context()
        if ctx.meta.cache_mode != "ttl":
            raise TypeError("expire_at() only applies to objects with cache_mode='ttl'")
        self._check_owner_permission(ctx.meta)
        seconds = max(0, int((when - datetime.datetime.now(datetime.UTC)).total_seconds()))
        if seconds == 0:
            self._redis().delete(ctx.key)
            return
        policy = TTLPolicy(cache_mode="ttl", declared_ttl=seconds)
        self.set_lifecycle(policy)

    def persist(self) -> None:
        from .lifecycle import TTLPolicy

        ctx = self._load_lifecycle_context()
        self._check_owner_permission(ctx.meta)
        policy = TTLPolicy(cache_mode="permanent", declared_ttl=0)
        self.set_lifecycle(policy)

    def remaining_ttl(self) -> int:
        return self._redis().ttl(self._canonical_redis_key())

    __hash__ = None

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, GPDCDataObject):
            return NotImplemented
        return (
            self.domain.name == other.domain.name
            and self._namespace.name == other._namespace.name
            and self._canonical_name == other._canonical_name
        )

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(uri={self.uri!r})"
