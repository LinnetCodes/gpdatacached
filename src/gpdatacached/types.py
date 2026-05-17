from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .namespace import GPDCNamespace

from .codec import GPDCScalarCodec, SetCodec
from .keys import make_object_key, make_raw_key, make_ref_key
from ._base import GPDCDataObject


@dataclass
class TypeEntry:
    python_type: type | tuple[type, ...]
    gpdc_type: str
    object_class: type[GPDCDataObject]
    codec_class: type | None
    priority: int

    @property
    def is_container(self) -> bool:
        from .container import GPDCContainerObject

        return issubclass(self.object_class, GPDCContainerObject)


class GPDCTypeRegistry:
    _entries: list[TypeEntry] = []

    @classmethod
    def register(
        cls,
        *,
        python_type: type | tuple[type, ...],
        gpdc_type: str,
        object_class: type[GPDCDataObject],
        codec_class: type | None = None,
        priority: int = 0,
    ) -> None:
        cls._entries = [e for e in cls._entries if e.gpdc_type != gpdc_type]
        cls._entries.append(
            TypeEntry(
                python_type=python_type,
                gpdc_type=gpdc_type,
                object_class=object_class,
                codec_class=codec_class,
                priority=priority,
            )
        )
        cls._entries.sort(key=lambda e: -e.priority)

    @classmethod
    def lookup_by_python_type(cls, value: object) -> TypeEntry:
        for entry in cls._entries:
            if isinstance(value, entry.python_type):
                return entry
        raise TypeError(f"Unsupported Python type: {type(value).__name__!r}")

    @classmethod
    def lookup_by_gpdc_type(cls, gpdc_type: str) -> TypeEntry:
        for entry in cls._entries:
            if entry.gpdc_type == gpdc_type:
                return entry
        raise ValueError(f"Unknown GPDC type: {gpdc_type!r}")


def encode_element(
    value: object,
    namespace: GPDCNamespace,
    owner_name: str,
    cache_mode: str,
    ttl: int,
) -> str:
    from .container import GPDCContainerObject

    if isinstance(value, GPDCContainerObject):
        return _ref_existing_container(namespace, value, owner_name)
    entry = GPDCTypeRegistry.lookup_by_python_type(value)
    if entry.is_container:
        _, ref_val = _create_anonymous_object(namespace, value, owner_name, cache_mode, ttl)
        return ref_val
    if entry.codec_class is not None:
        return entry.codec_class.encode_value(value)
    return GPDCScalarCodec.encode_value(value)


def decode_element(encoded: str, namespace: GPDCNamespace) -> object:
    if isinstance(encoded, str) and encoded.startswith("ref:"):
        return _load_ref_object(namespace, encoded)
    return GPDCScalarCodec.decode_value(encoded)


def _ref_existing_container(
    namespace: GPDCNamespace,
    container: GPDCDataObject,
    parent_name: str,
) -> str:
    from .container import GPDCContainerObject

    assert isinstance(container, GPDCContainerObject)
    obj_key = container._redis_key()
    ref_key = make_ref_key(namespace.domain.name, namespace.name, container._name)
    namespace._redis().sadd(ref_key, parent_name)
    return f"ref:{obj_key}"


def _load_ref_object(namespace: GPDCNamespace, ref_str: str):
    from .container import GPDCContainerObject

    obj_key = ref_str[len("ref:") :]
    parts = obj_key.split(":obj:")
    if len(parts) != 2:
        raise ValueError(f"Invalid ref string: {ref_str!r}")
    anon_name = parts[1]
    r = namespace._redis()
    meta = r.hgetall(obj_key)
    if not meta:
        raise KeyError(f"Referenced object not found: {obj_key!r}")
    type_str = meta.get("type", "")
    entry = GPDCTypeRegistry.lookup_by_gpdc_type(type_str)
    obj = entry.object_class(anon_name, namespace)
    if isinstance(obj, GPDCContainerObject):
        stored_raw_key = meta.get("value", "")
        if stored_raw_key:
            obj.raw_key = stored_raw_key
    return obj


def _create_anonymous_object(
    namespace: GPDCNamespace,
    python_value: object,
    parent_name: str,
    cache_mode: str,
    ttl: int,
) -> tuple[str, str]:
    entry = GPDCTypeRegistry.lookup_by_python_type(python_value)
    now = datetime.datetime.now(datetime.UTC).isoformat()

    anon_name = f"~anon:{uuid.uuid4()}"
    obj_key = make_object_key(namespace.domain.name, namespace.name, anon_name)
    raw_key = make_raw_key(namespace.domain.name, namespace.name, anon_name)

    r = namespace._redis()
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

    if isinstance(python_value, list):
        encoded_items = [encode_element(v, namespace, anon_name, cache_mode, ttl) for v in python_value]
        if encoded_items:
            r.rpush(raw_key, *encoded_items)
    elif isinstance(python_value, dict):
        raw_mapping = {}
        for k, v in python_value.items():
            encoded_key = GPDCScalarCodec.encode_dict_key(k)
            raw_mapping[encoded_key] = encode_element(v, namespace, anon_name, cache_mode, ttl)
        if raw_mapping:
            r.hset(raw_key, mapping=raw_mapping)
    elif isinstance(python_value, set):
        encoded_items = [SetCodec._encode_element(v) for v in python_value]
        if encoded_items:
            r.sadd(raw_key, *encoded_items)

    ref_key = make_ref_key(namespace.domain.name, namespace.name, anon_name)
    r.sadd(ref_key, parent_name)

    if cache_mode in ("ttl", "sliding") and ttl > 0:
        r.expire(obj_key, ttl)

    ref_val = f"ref:{obj_key}"
    return anon_name, ref_val


def _register_builtin_types() -> None:
    from .codec import GPDCScalarCodec, ListCodec, DictCodec, SetCodec
    from .scalar import (
        IntObject,
        FloatObject,
        StrObject,
        BoolObject,
        DateTimeObject,
        DateObject,
        TimeObject,
        NoneObject,
    )
    from .container import ListObject, DictObject, SetObject

    GPDCTypeRegistry.register(
        python_type=type(None),
        gpdc_type="scalar:none",
        object_class=NoneObject,
        codec_class=GPDCScalarCodec,
        priority=0,
    )
    GPDCTypeRegistry.register(
        python_type=bool, gpdc_type="scalar:bool", object_class=BoolObject, codec_class=GPDCScalarCodec, priority=10
    )
    GPDCTypeRegistry.register(
        python_type=int, gpdc_type="scalar:int", object_class=IntObject, codec_class=GPDCScalarCodec, priority=0
    )
    GPDCTypeRegistry.register(
        python_type=float, gpdc_type="scalar:float", object_class=FloatObject, codec_class=GPDCScalarCodec, priority=0
    )
    GPDCTypeRegistry.register(
        python_type=str, gpdc_type="scalar:str", object_class=StrObject, codec_class=GPDCScalarCodec, priority=0
    )
    GPDCTypeRegistry.register(
        python_type=datetime.datetime,
        gpdc_type="scalar:datetime",
        object_class=DateTimeObject,
        codec_class=GPDCScalarCodec,
        priority=0,
    )
    GPDCTypeRegistry.register(
        python_type=datetime.date,
        gpdc_type="scalar:date",
        object_class=DateObject,
        codec_class=GPDCScalarCodec,
        priority=0,
    )
    GPDCTypeRegistry.register(
        python_type=datetime.time,
        gpdc_type="scalar:time",
        object_class=TimeObject,
        codec_class=GPDCScalarCodec,
        priority=0,
    )

    GPDCTypeRegistry.register(
        python_type=list, gpdc_type="collection:list", object_class=ListObject, codec_class=ListCodec, priority=0
    )
    GPDCTypeRegistry.register(
        python_type=dict, gpdc_type="collection:dict", object_class=DictObject, codec_class=DictCodec, priority=0
    )
    GPDCTypeRegistry.register(
        python_type=set, gpdc_type="collection:set", object_class=SetObject, codec_class=SetCodec, priority=0
    )


_register_builtin_types()
