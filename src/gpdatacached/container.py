# src/gpdatacached/container.py
"""GPDC container object hierarchy."""

from __future__ import annotations

import datetime
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .namespace import GPDCNamespace

from .keys import make_object_key, make_raw_key, make_ref_key
from .objects import GPDCDataObject
from .meta import ObjectMeta
from .types import decode_element


class GPDCContainerObject(GPDCDataObject, ABC):
    """Abstract base class for all container GPDC objects."""

    def __init__(self, name: str, namespace: "GPDCNamespace", *, canonical_name: str | None = None) -> None:
        super().__init__(name, namespace, canonical_name=canonical_name)
        domain_name = namespace.domain.name
        ns_name = namespace.name
        self.raw_key: str = make_raw_key(domain_name, ns_name, name)
        self.ref_key: str = make_ref_key(domain_name, ns_name, name)

    def _touch(self) -> None:
        from .ttl import policy_from_meta, policy_for_sliding_touch
        from .meta import ObjectMeta
        from .keys import make_object_key
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
            owner_name = meta.owner
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

    @abstractmethod
    def _current_item_count(self) -> int:
        """Return the current element count from Redis."""

    def _unbind_old_ref(self, old_encoded: str) -> None:
        if not isinstance(old_encoded, str) or not old_encoded.startswith("ref:"):
            return
        if self._owner_still_refs_encoded(old_encoded):
            return
        obj_key = old_encoded[len("ref:") :]
        parts = obj_key.split(":obj:")
        if len(parts) != 2:
            return
        target_name = parts[1]
        target_ref_key = make_ref_key(self._namespace.domain.name, self._namespace.name, target_name)
        r = self._redis()
        r.srem(target_ref_key, self._binding_name())
        if target_name.startswith("~") and not r.smembers(target_ref_key):
            target_obj_key = make_object_key(self._namespace.domain.name, self._namespace.name, target_name)
            target_meta = ObjectMeta.load(r, target_obj_key)
            if target_meta is None or target_meta.owner == self._binding_name():
                self._namespace._cascade_delete_anonymous(target_name)

    def _owner_still_refs_encoded(self, encoded: str) -> bool:
        if not self.raw_key:
            return False
        r = self._redis()
        redis_type = r.type(self.raw_key)
        if redis_type == "list":
            return encoded in r.lrange(self.raw_key, 0, -1)
        if redis_type == "hash":
            return encoded in r.hvals(self.raw_key)
        return False

    def _cleanup_slot_overwrite(self, old_encoded: str | None) -> None:
        if old_encoded is None:
            return
        if not self._is_container_ref(old_encoded):
            self._unbind_old_ref(old_encoded)
            return
        obj_key = old_encoded[len("ref:") :]
        parts = obj_key.split(":obj:")
        if len(parts) != 2:
            return
        target_name = parts[1]
        target_obj_key = make_object_key(self._namespace.domain.name, self._namespace.name, target_name)
        r = self._redis()
        old_meta = ObjectMeta.load(r, target_obj_key)
        if old_meta is not None and old_meta.owner != self._binding_name():
            self._unbind_old_ref(old_encoded)
            return
        self._unbind_old_ref(old_encoded)

    def _reject_value_replacement(self) -> None:
        raise TypeError("Container value replacement is not supported; use explicit mutating methods instead")

    def _is_container_ref(self, encoded: str | None) -> bool:
        return isinstance(encoded, str) and encoded.startswith("ref:")

    def _materialize_decoded_value(self, encoded: str) -> object:
        value = decode_element(encoded, self._namespace)
        if self._is_container_ref(encoded) and hasattr(value, "deepcopy"):
            return value.deepcopy()
        return value

    def _binding_name(self) -> str:
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

    def _reference_obj_key(self) -> str:
        return make_object_key(self._namespace.domain.name, self._namespace.name, self._binding_name())

    def _reference_ref_value(self) -> str:
        return f"ref:{self._reference_obj_key()}"

    def referrers(self) -> set[str]:
        """Return set of object names referencing this object's raw data."""
        owner_name = self._binding_name()
        ref_key = make_ref_key(self._namespace.domain.name, self._namespace.name, owner_name)
        return self._redis().smembers(ref_key)

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
