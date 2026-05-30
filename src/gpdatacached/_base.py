# src/gpdatacached/_base.py
"""GPDCDataObject – abstract base class for all GPDC data objects.

Extracted from objects.py to break circular imports between scalar.py,
container.py, types.py and objects.py.
"""

from __future__ import annotations

import datetime
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .domain import GPDCDomain
    from .namespace import GPDCNamespace

from .keys import make_object_key, make_uri
from .meta import ObjectMeta


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
    def cache_mode(self) -> str:
        meta = ObjectMeta.load(self._redis(), self._canonical_redis_key())
        return meta.cache_mode if meta else ""

    @cache_mode.setter
    def cache_mode(self, mode: str) -> None:
        from .ttl import policy_from_meta, policy_for_cache_mode_change
        from .meta import apply_lifecycle
        from .keys import make_ref_key

        if mode not in ("permanent", "ttl", "sliding"):
            raise ValueError(f"Invalid cache_mode: {mode!r}")
        key = self._canonical_redis_key()
        r = self._redis()
        meta = ObjectMeta.load(r, key)
        if meta is None:
            return
        current = policy_from_meta(meta)
        meta.with_updates(cache_mode=mode).save(r, key)
        new_policy = policy_for_cache_mode_change(current, mode)
        is_collection = meta.is_collection()
        raw_key = meta.value if is_collection else ""
        ref_key = make_ref_key(self.domain.name, self.namespace.name, self._canonical_name) if is_collection else ""
        apply_lifecycle(r, key, raw_key, ref_key, is_collection, new_policy)

    @property
    def ttl(self) -> int:
        meta = ObjectMeta.load(self._redis(), self._canonical_redis_key())
        return meta.ttl if meta else 0

    @ttl.setter
    def ttl(self, seconds: int) -> None:
        from .ttl import policy_from_meta, policy_for_ttl_change
        from .meta import apply_lifecycle
        from .keys import make_ref_key

        key = self._canonical_redis_key()
        r = self._redis()
        meta = ObjectMeta.load(r, key)
        if meta is None:
            return
        current = policy_from_meta(meta)
        meta.with_updates(ttl=seconds).save(r, key)
        new_policy = policy_for_ttl_change(current, seconds)
        is_collection = meta.is_collection()
        raw_key = meta.value if is_collection else ""
        ref_key = make_ref_key(self.domain.name, self.namespace.name, self._canonical_name) if is_collection else ""
        apply_lifecycle(r, key, raw_key, ref_key, is_collection, new_policy)

    @property
    def created_at(self) -> datetime.datetime | None:
        meta = ObjectMeta.load(self._redis(), self._canonical_redis_key())
        return meta.created_at if meta else None

    @property
    def updated_at(self) -> datetime.datetime | None:
        meta = ObjectMeta.load(self._redis(), self._canonical_redis_key())
        return meta.updated_at if meta else None

    def expire_in(self, seconds: int) -> None:
        from .meta import apply_lifecycle
        from .ttl import TTLPolicy
        from .keys import make_ref_key

        key = self._canonical_redis_key()
        r = self._redis()
        meta = ObjectMeta.load(r, key)
        if meta is None:
            return
        is_collection = meta.is_collection()
        raw_key = meta.value if is_collection else ""
        ref_key = make_ref_key(self.domain.name, self.namespace.name, self._canonical_name) if is_collection else ""
        policy = TTLPolicy(cache_mode="ttl", declared_ttl=seconds, expire_seconds=seconds)
        apply_lifecycle(r, key, raw_key, ref_key, is_collection, policy)

    def expire_at(self, when: datetime.datetime) -> None:
        from .meta import apply_lifecycle
        from .ttl import TTLPolicy
        from .keys import make_ref_key

        key = self._canonical_redis_key()
        r = self._redis()
        meta = ObjectMeta.load(r, key)
        if meta is None:
            return
        seconds = max(0, int((when - datetime.datetime.now(datetime.UTC)).total_seconds()))
        is_collection = meta.is_collection()
        raw_key = meta.value if is_collection else ""
        ref_key = make_ref_key(self.domain.name, self.namespace.name, self._canonical_name) if is_collection else ""
        policy = TTLPolicy(cache_mode="ttl", declared_ttl=seconds, expire_seconds=seconds)
        apply_lifecycle(r, key, raw_key, ref_key, is_collection, policy)

    def persist(self) -> None:
        from .ttl import policy_from_meta, TTLPolicy
        from .meta import apply_lifecycle
        from .keys import make_ref_key

        key = self._canonical_redis_key()
        r = self._redis()
        meta = ObjectMeta.load(r, key)
        if meta is None:
            return
        current = policy_from_meta(meta)
        is_collection = meta.is_collection()
        raw_key = meta.value if is_collection else ""
        ref_key = make_ref_key(self.domain.name, self.namespace.name, self._canonical_name) if is_collection else ""
        apply_lifecycle(
            r,
            key,
            raw_key,
            ref_key,
            is_collection,
            TTLPolicy(
                cache_mode=current.cache_mode,
                declared_ttl=current.declared_ttl,
                expire_seconds=None,
            ),
        )

    def remaining_ttl(self) -> int:
        return self._redis().ttl(self._canonical_redis_key())
