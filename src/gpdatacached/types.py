from __future__ import annotations

import contextlib
import contextvars
import datetime
import threading
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .namespace import GPDCNamespace

from .keys import make_object_key, make_raw_key, make_ref_key, parse_ref_encoded
from .object import GPDCDataObject
from .refset import add_referrer


_encode_memo_var: contextvars.ContextVar[dict[int, tuple[str, str | None]] | None] = contextvars.ContextVar(
    "_encode_memo", default=None
)


@contextlib.contextmanager
def encode_session(namespace: GPDCNamespace | None = None):
    """Establish an encode memo scope for cycle detection and aliasing.

    Must wrap any top-level encode operation that makes multiple sibling
    ``encode_element`` calls (e.g. container ``create_raw``,
    ``ListObject.extend``, ``DictObject.update``). Single-call paths
    (``append``, ``__setitem__``) are handled by a fallback session inside
    ``encode_element`` itself.

    If ``namespace`` is provided, any anonymous objects created during the
    session are rolled back (deleted from Redis) when an exception propagates
    out of the session.
    """
    memo: dict[int, tuple[str, str | None]] = {}
    token = _encode_memo_var.set(memo)
    ok = False
    try:
        yield
        ok = True
    finally:
        _encode_memo_var.reset(token)
        if not ok and namespace is not None:
            _rollback_encode_memo(namespace, memo)


@dataclass(frozen=True)
class TypeEntry:
    python_type: type | tuple[type, ...]
    gpdc_type: str
    object_class: type[GPDCDataObject]
    codec_class: type
    priority: int

    @property
    def is_container(self) -> bool:
        from .container import GPDCContainerObject

        return issubclass(self.object_class, GPDCContainerObject)


class GPDCTypeRegistry:
    _by_gpdc_type: dict[str, TypeEntry] = {}
    _by_python_type: list[tuple[type | tuple[type, ...], TypeEntry]] = []
    _lock = threading.Lock()

    @classmethod
    def register(
        cls,
        *,
        python_type: type | tuple[type, ...],
        gpdc_type: str,
        object_class: type[GPDCDataObject],
        codec_class: type,
        priority: int = 0,
    ) -> None:
        entry = TypeEntry(
            python_type=python_type,
            gpdc_type=gpdc_type,
            object_class=object_class,
            codec_class=codec_class,
            priority=priority,
        )
        with cls._lock:
            cls._by_gpdc_type[gpdc_type] = entry
            cls._by_python_type = [(pt, e) for pt, e in cls._by_python_type if e.gpdc_type != gpdc_type]
            cls._by_python_type.append((python_type, entry))
            cls._by_python_type.sort(key=lambda pair: -pair[1].priority)

    @classmethod
    def lookup_by_python_type(cls, value: object) -> TypeEntry:
        with cls._lock:
            for python_type, entry in cls._by_python_type:
                if isinstance(value, python_type):
                    return entry
        raise TypeError(f"Unsupported Python type: {type(value).__name__!r}")

    @classmethod
    def lookup_by_gpdc_type(cls, gpdc_type: str) -> TypeEntry:
        with cls._lock:
            entry = cls._by_gpdc_type.get(gpdc_type)
        if entry is None:
            raise ValueError(f"Unknown GPDC type: {gpdc_type!r}")
        return entry


def encode_element(
    value: object,
    namespace: GPDCNamespace,
    owner_name: str,
    policy,
) -> str:
    from .container import GPDCContainerObject

    if isinstance(value, GPDCContainerObject):
        return _ref_existing_container(namespace, value, owner_name)
    entry = GPDCTypeRegistry.lookup_by_python_type(value)
    if entry.is_container:
        if _encode_memo_var.get() is None:
            with encode_session(namespace):
                return _encode_container_value(value, namespace, owner_name, policy)
        return _encode_container_value(value, namespace, owner_name, policy)
    return entry.codec_class.encode_value(value)


def _encode_container_value(
    value: object,
    namespace: GPDCNamespace,
    owner_name: str,
    policy,
) -> str:
    from .exceptions import CyclicReferenceError

    memo = _encode_memo_var.get()
    assert memo is not None
    vid = id(value)
    if vid in memo:
        state, ref_val = memo[vid]
        if state == "in-progress":
            raise CyclicReferenceError(type(value).__name__)
        _add_alias_ref(namespace, ref_val, owner_name)
        return ref_val
    memo[vid] = ("in-progress", None)
    try:
        _, ref_val = _create_anonymous_object(namespace, value, owner_name, policy)
        memo[vid] = ("complete", ref_val)
    except BaseException:
        memo.pop(vid, None)
        raise
    return ref_val


def _add_alias_ref(namespace: GPDCNamespace, ref_val: str, owner_name: str) -> None:
    """Bump the reference count for an aliased anonymous object."""
    anon_name = parse_ref_encoded(ref_val)
    if anon_name is None:
        return
    ref_key = make_ref_key(namespace.domain.name, namespace.name, anon_name)
    add_referrer(namespace._redis(), ref_key, owner_name)


def _rollback_encode_memo(namespace: GPDCNamespace, memo: dict[int, tuple[str, str | None]]) -> None:
    """Delete all anonymous objects created during a failed encode session."""
    r = namespace._redis()
    for state, ref_val in memo.values():
        if state != "complete" or not ref_val:
            continue
        anon_name = parse_ref_encoded(ref_val)
        if anon_name is None:
            continue
        obj_key = ref_val[len("ref:") :]
        raw_key = make_raw_key(namespace.domain.name, namespace.name, anon_name)
        ref_key = make_ref_key(namespace.domain.name, namespace.name, anon_name)
        r.delete(obj_key, raw_key, ref_key)


def _decode_scalar(encoded: str) -> object:
    if not encoded or ":" not in encoded:
        raise ValueError(f"Invalid encoding: {encoded!r}")
    colon_idx = encoded.index(":")
    type_name = encoded[:colon_idx]
    gpdc_type = f"scalar:{type_name}"
    entry = GPDCTypeRegistry.lookup_by_gpdc_type(gpdc_type)
    return entry.codec_class.decode_value(encoded)


def decode_element(encoded: str, namespace: GPDCNamespace) -> object:
    if encoded.startswith("ref:"):
        return _load_ref_object(namespace, encoded)
    return _decode_scalar(encoded)


def encode_key(value: object) -> str:
    entry = GPDCTypeRegistry.lookup_by_python_type(value)
    if entry.is_container:
        raise TypeError(f"Cannot use container type as key: {type(value).__name__!r}")
    if isinstance(value, tuple) and len(value) == 0:
        raise TypeError("Cannot use empty tuple as key")
    return entry.codec_class.encode_value(value)


def decode_key(encoded: str) -> object:
    return _decode_scalar(encoded)


def _ref_existing_container(
    namespace: GPDCNamespace,
    container: GPDCDataObject,
    parent_name: str,
) -> str:
    from .container import GPDCContainerObject

    assert isinstance(container, GPDCContainerObject)
    if container._namespace is not namespace:
        raise ValueError("Cross-namespace container reference sharing is not supported")
    owner_name = container._data_owner_name()
    obj_key = make_object_key(namespace.domain.name, namespace.name, owner_name)
    ref_key = make_ref_key(namespace.domain.name, namespace.name, owner_name)
    add_referrer(namespace._redis(), ref_key, parent_name)
    return f"ref:{obj_key}"


def _load_ref_object(namespace: GPDCNamespace, ref_str: str):
    from .container import GPDCContainerObject
    from .meta import ObjectMeta

    anon_name = parse_ref_encoded(ref_str)
    if anon_name is None:
        raise ValueError(f"Invalid ref string: {ref_str!r}")
    obj_key = ref_str[len("ref:") :]
    r = namespace._redis()
    meta = ObjectMeta.load(r, obj_key)
    if meta is None:
        raise KeyError(f"Referenced object not found: {obj_key!r}")
    entry = GPDCTypeRegistry.lookup_by_gpdc_type(meta.type)
    obj = entry.object_class(anon_name, namespace)
    if isinstance(obj, GPDCContainerObject):
        if meta.value:
            obj.raw_key = meta.value
    return obj


def _create_anonymous_object(
    namespace: GPDCNamespace,
    python_value: object,
    parent_name: str,
    policy,
) -> tuple[str, str]:
    from .lifecycle import apply_ttl
    from .meta import ObjectMeta

    entry = GPDCTypeRegistry.lookup_by_python_type(python_value)
    now = datetime.datetime.now(datetime.UTC)

    anon_name = f"~anon:{uuid.uuid4()}"
    obj_key = make_object_key(namespace.domain.name, namespace.name, anon_name)
    raw_key = make_raw_key(namespace.domain.name, namespace.name, anon_name)
    ref_key = make_ref_key(namespace.domain.name, namespace.name, anon_name)

    r = namespace._redis()
    meta = ObjectMeta(
        type=entry.gpdc_type,
        value=raw_key,
        cache_mode=policy.cache_mode,
        ttl=policy.declared_ttl,
        owner=parent_name,
        created_at=now,
        updated_at=now,
        item_count=(len(python_value) if entry.is_container and hasattr(python_value, "__len__") else None),
    )
    meta.save(r, obj_key)

    if entry.is_container:
        from .codec import GPDCContainerCodec

        if issubclass(entry.codec_class, GPDCContainerCodec):
            try:
                entry.codec_class.create_raw(
                    namespace, raw_key, python_value, anon_name, policy.cache_mode, policy.declared_ttl
                )
            except BaseException:
                r.delete(obj_key, raw_key, ref_key)
                raise

    add_referrer(r, ref_key, parent_name)

    keys = [obj_key]
    if entry.is_container:
        keys.extend([raw_key, ref_key])
    apply_ttl(r, keys, policy)

    ref_val = f"ref:{obj_key}"
    return anon_name, ref_val
