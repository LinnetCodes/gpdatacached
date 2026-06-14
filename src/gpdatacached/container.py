# src/gpdatacached/container.py
"""GPDC container object hierarchy."""

from __future__ import annotations

import datetime
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .namespace import GPDCNamespace

from .keys import make_object_key, make_raw_key, make_ref_key, parse_ref_encoded
from .objects import GPDCDataObject
from .meta import ObjectMeta
from .refset import release_ref, get_referrers, has_referrers
from .types import decode_element


class GPDCContainerObject(GPDCDataObject, ABC):
    """Abstract base for container GPDC objects (lists, dicts, sets).

    A container object stores its elements in a dedicated Redis key
    (``raw_key``) and tracks which other objects reference its raw data via
    a Redis hash (``ref_key``). Subclasses implement ``MutableSequence``,
    ``MutableMapping``, or ``MutableSet`` semantics on top of this shared
    infrastructure.

    Key attributes:
        raw_key: Redis key holding the container's element data (e.g. a list
            or hash).
        ref_key: Redis hash key recording referrer names and their reference
            counts; used for ref-counting and cascade-deletion of anonymous
            objects.
    """

    def __init__(self, name: str, namespace: "GPDCNamespace", *, canonical_name: str | None = None) -> None:
        super().__init__(name, namespace, canonical_name=canonical_name)
        domain_name = namespace.domain.name
        ns_name = namespace.name
        self.raw_key: str = make_raw_key(domain_name, ns_name, name)
        self.ref_key: str = make_ref_key(domain_name, ns_name, name)

    # ── Lifecycle: timestamps & sliding TTL ──────────────────────────

    def _touch(self) -> None:
        """Update metadata timestamps and apply sliding TTL if configured.

        When owned by a parent, the sliding refresh propagates upward
        through the owner chain.
        """
        from .ttl import policy_from_meta, policy_for_sliding_touch
        from .meta import ObjectMeta
        from .exceptions import ObjectGoneError

        key = self._canonical_redis_key()
        now = datetime.datetime.now(datetime.UTC)
        r = self._redis()
        item_count = self._current_item_count()
        meta = ObjectMeta.load(r, key)
        if meta is None:
            raise ObjectGoneError(self.canonical_uri)
        meta.touch(now, item_count=item_count).save(r, key)

        if meta.owner == self.canonical_name:
            current = policy_from_meta(meta)
            new_policy = policy_for_sliding_touch(current)
            if new_policy != current:
                self.set_lifecycle(new_policy)
        else:
            self._propagate_sliding_ttl_upward(meta.owner, r)

    def _current_item_count(self) -> int:
        """Return the current number of elements in this container."""
        return len(self)

    def _propagate_sliding_ttl_upward(self, owner_name: str, r) -> None:
        """Propagate a sliding-window TTL refresh up the owner chain to the first self-owned ancestor."""
        from .ttl import policy_from_meta, policy_for_sliding_touch
        from .keys import make_object_key

        while owner_name:
            owner_obj_key = make_object_key(self.domain.name, self.namespace.name, owner_name)
            owner_meta = ObjectMeta.load(r, owner_obj_key)
            if owner_meta is None:
                break
            if owner_meta.owner == owner_name:
                current = policy_from_meta(owner_meta)
                new_policy = policy_for_sliding_touch(current)
                if new_policy != current:
                    owner_obj = self.namespace.get_object(owner_name)
                    owner_obj.set_lifecycle(new_policy)
                break
            owner_name = owner_meta.owner

    # ── Identity: data owner resolution & encoding ───────────────────

    def _data_owner_name(self) -> str:
        """Return the canonical name that owns this container's raw data.

        For references, returns ``canonical_name``; for owners, loads meta
        to verify the raw_key mapping.
        """
        if self._is_reference:
            return self._canonical_name
        meta = ObjectMeta.load(self._redis(), self._redis_key())
        if meta is None:
            return self._name
        raw_key = meta.value or self.raw_key
        prefix = f"gpdc:{self._namespace.domain.name}:{self._namespace.name}:raw:"
        if raw_key.startswith(prefix):
            self.raw_key = raw_key
            return raw_key[len(prefix) :]
        return self._name

    def _encode_as_ref_value(self) -> str:
        """Encode this container as a ``ref:`` string for storage in parent raw data."""
        owner_name = self._data_owner_name()
        obj_key = make_object_key(self._namespace.domain.name, self._namespace.name, owner_name)
        return f"ref:{obj_key}"

    # ── Element reference tracking ────────────────────────────────────

    def _release_element_ref(self, old_encoded: str | None) -> None:
        """Release the ref binding held by a removed or overwritten element.

        Decrements the reference count in the target's ref hash. If the
        count reaches zero, cascade-deletes the target if it is an
        orphaned anonymous object.
        """
        if not isinstance(old_encoded, str) or not old_encoded.startswith("ref:"):
            return
        target_name = parse_ref_encoded(old_encoded)
        if target_name is None:
            return
        target_ref_key = make_ref_key(self._namespace.domain.name, self._namespace.name, target_name)
        fully_released = release_ref(self._redis(), target_ref_key, self._data_owner_name())
        if fully_released:
            self._gc_anonymous_if_orphaned(target_name)

    def _gc_anonymous_if_orphaned(self, target_name: str) -> None:
        """Cascade-delete an anonymous object if it has no remaining referrers."""
        if not target_name.startswith("~"):
            return
        target_ref_key = make_ref_key(self._namespace.domain.name, self._namespace.name, target_name)
        r = self._redis()
        if has_referrers(r, target_ref_key):
            return
        target_obj_key = make_object_key(self._namespace.domain.name, self._namespace.name, target_name)
        target_meta = ObjectMeta.load(r, target_obj_key)
        if target_meta is None or target_meta.owner == self._data_owner_name():
            self._namespace._cascade_delete_anonymous(target_name)

    # ── Value decode helpers ──────────────────────────────────────────

    def _is_ref_encoded(self, encoded: str | None) -> bool:
        """Check whether ``encoded`` value is a ``ref:`` pointer string."""
        return isinstance(encoded, str) and encoded.startswith("ref:")

    def _decode_and_copy_value(self, encoded: str) -> object:
        """Decode an encoded element; deep-copy if it resolves to a container."""
        value = decode_element(encoded, self._namespace)
        if self._is_ref_encoded(encoded) and hasattr(value, "deepcopy"):
            return value.deepcopy()
        return value

    def _reject_value_replacement(self) -> None:
        """Always raise — containers require explicit mutation methods."""
        raise TypeError("Container value replacement is not supported; use explicit mutating methods instead")

    # ── Public inspection API ─────────────────────────────────────────

    def referrers(self) -> set[str]:
        """Return the set of object names referencing this container's raw data."""
        owner_name = self._data_owner_name()
        ref_key = make_ref_key(self._namespace.domain.name, self._namespace.name, owner_name)
        return get_referrers(self._redis(), ref_key)

    def copy(self) -> object:
        """Shallow copy: return Python value with shared anon object refs."""
        return self.value

    def deepcopy(self) -> object:
        """Deep copy: recursively clone all anon objects, return Python value."""
        raise NotImplementedError("Subclass must implement deepcopy()")

    @property
    @abstractmethod
    def value(self) -> object: ...

    @value.setter
    @abstractmethod
    def value(self, new_value: object) -> None: ...
