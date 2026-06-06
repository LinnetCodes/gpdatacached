from __future__ import annotations

import datetime
import threading
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .namespace import GPDCNamespace

from .keys import make_object_key, make_raw_key, make_ref_key
from .objects import GPDCDataObject


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
        _, ref_val = _create_anonymous_object(namespace, value, owner_name, policy)
        return ref_val
    return entry.codec_class.encode_value(value)


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
    owner_name = container._binding_name()
    obj_key = make_object_key(namespace.domain.name, namespace.name, owner_name)
    ref_key = make_ref_key(namespace.domain.name, namespace.name, owner_name)
    namespace._redis().sadd(ref_key, parent_name)
    return f"ref:{obj_key}"


def _load_ref_object(namespace: GPDCNamespace, ref_str: str):
    from .container import GPDCContainerObject
    from .meta import ObjectMeta

    obj_key = ref_str[len("ref:") :]
    parts = obj_key.split(":obj:")
    if len(parts) != 2:
        raise ValueError(f"Invalid ref string: {ref_str!r}")
    anon_name = parts[1]
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
    from .ttl import apply_lifecycle
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
        item_count=len(python_value) if entry.is_container else None,
    )
    meta.save(r, obj_key)

    if entry.is_container:
        from .codec import GPDCContainerCodec

        if issubclass(entry.codec_class, GPDCContainerCodec):
            entry.codec_class.create_raw(
                namespace, raw_key, python_value, anon_name, policy.cache_mode, policy.declared_ttl
            )

    r.sadd(ref_key, parent_name)

    apply_lifecycle(r, obj_key, raw_key, ref_key, entry.is_container, policy)

    ref_val = f"ref:{obj_key}"
    return anon_name, ref_val


def _register_builtin_types() -> None:
    from .codec import (
        NoneCodec,
        BoolCodec,
        IntCodec,
        FloatCodec,
        StrCodec,
        DateTimeCodec,
        DateCodec,
        TimeCodec,
        TupleCodec,
        ListCodec,
        DictCodec,
        SetCodec,
    )
    from .scalar import (
        IntObject,
        FloatObject,
        StrObject,
        BoolObject,
        DateTimeObject,
        DateObject,
        TimeObject,
        NoneObject,
        TupleObject,
    )
    from .container import ListObject, DictObject, SetObject

    GPDCTypeRegistry.register(
        python_type=type(None),
        gpdc_type="scalar:none",
        object_class=NoneObject,
        codec_class=NoneCodec,
        priority=0,
    )
    GPDCTypeRegistry.register(
        python_type=bool,
        gpdc_type="scalar:bool",
        object_class=BoolObject,
        codec_class=BoolCodec,
        priority=10,
    )
    GPDCTypeRegistry.register(
        python_type=int,
        gpdc_type="scalar:int",
        object_class=IntObject,
        codec_class=IntCodec,
        priority=0,
    )
    GPDCTypeRegistry.register(
        python_type=float,
        gpdc_type="scalar:float",
        object_class=FloatObject,
        codec_class=FloatCodec,
        priority=0,
    )
    GPDCTypeRegistry.register(
        python_type=str,
        gpdc_type="scalar:str",
        object_class=StrObject,
        codec_class=StrCodec,
        priority=0,
    )
    GPDCTypeRegistry.register(
        python_type=datetime.datetime,
        gpdc_type="scalar:datetime",
        object_class=DateTimeObject,
        codec_class=DateTimeCodec,
        priority=10,
    )
    GPDCTypeRegistry.register(
        python_type=datetime.date,
        gpdc_type="scalar:date",
        object_class=DateObject,
        codec_class=DateCodec,
        priority=0,
    )
    GPDCTypeRegistry.register(
        python_type=datetime.time,
        gpdc_type="scalar:time",
        object_class=TimeObject,
        codec_class=TimeCodec,
        priority=0,
    )
    GPDCTypeRegistry.register(
        python_type=tuple,
        gpdc_type="scalar:tuple",
        object_class=TupleObject,
        codec_class=TupleCodec,
        priority=0,
    )

    GPDCTypeRegistry.register(
        python_type=list,
        gpdc_type="collection:list",
        object_class=ListObject,
        codec_class=ListCodec,
        priority=0,
    )
    GPDCTypeRegistry.register(
        python_type=dict,
        gpdc_type="collection:dict",
        object_class=DictObject,
        codec_class=DictCodec,
        priority=0,
    )
    GPDCTypeRegistry.register(
        python_type=set,
        gpdc_type="collection:set",
        object_class=SetObject,
        codec_class=SetCodec,
        priority=0,
    )


_register_builtin_types()
