"""GPDC namespace: a MutableMapping-compatible logical isolation space for data objects."""

from __future__ import annotations

import datetime
from collections.abc import MutableMapping
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from .config import GPDCNamespaceConfig
    from .domain import GPDCDomain

from .codec import GPDCScalarCodec
from .keys import (
    make_domain_namespaces_key,
    make_namespace_meta_key,
    make_object_key,
    make_object_key_prefix,
    validate_namespace_name,
    validate_object_name,
)
from .objects import SCALAR_TYPE_CLASS_MAP, GPDCScalarObject


class GPDCNamespace(MutableMapping):
    """Logical isolation space within a GPDCDomain.

    Implements the full ``MutableMapping`` interface backed by Redis.
    Keys are named object names; values are ``GPDCScalarObject`` instances.
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

    def __setitem__(self, key: str, value: object) -> None:
        validate_object_name(key)

        # Unwrap GPDCScalarObject → Python value (copy semantics)
        if isinstance(value, GPDCScalarObject):
            value = value.value

        gpdc_type = GPDCScalarCodec.gpdc_type_of(value)
        encoded = GPDCScalarCodec.encode_value(value)
        obj_key = make_object_key(self._domain.name, self._name, key)
        now = datetime.datetime.now(datetime.UTC).isoformat()
        r = self._redis()

        existing = r.hgetall(obj_key)
        if existing:
            # Update existing object: preserve cache_mode/ttl, update value/type/updated_at
            cache_mode = existing.get("cache_mode", "permanent")
            ttl_raw = existing.get("ttl", "0")
            ttl = int(ttl_raw) if ttl_raw else 0
            pipe = r.pipeline()
            pipe.hset(obj_key, mapping={"type": gpdc_type, "value": encoded, "updated_at": now})
            if cache_mode == "sliding" and ttl > 0:
                pipe.expire(obj_key, ttl)
            pipe.execute()
        else:
            # Create new object
            cache_mode = self._get_default_cache_mode()
            ttl = self._get_default_ttl_seconds()
            pipe = r.pipeline()
            pipe.hset(
                obj_key,
                mapping={
                    "type": gpdc_type,
                    "codec": "native",
                    "value": encoded,
                    "cache_mode": cache_mode,
                    "ttl": str(ttl) if ttl else "",
                    "created_at": now,
                    "updated_at": now,
                },
            )
            if cache_mode in ("ttl", "sliding") and ttl > 0:
                pipe.expire(obj_key, ttl)
            pipe.execute()

    def __getitem__(self, key: str) -> GPDCScalarObject:
        validate_object_name(key)
        obj_key = make_object_key(self._domain.name, self._name, key)
        type_str = self._redis().hget(obj_key, "type")
        if type_str is None:
            raise KeyError(f"{key!r} not found in namespace {self._name!r}")
        cls = SCALAR_TYPE_CLASS_MAP.get(type_str)
        if cls is None:
            raise ValueError(f"Unknown GPDC type: {type_str!r}")
        return cls(key, self)

    def __delitem__(self, key: str) -> None:
        validate_object_name(key)
        obj_key = make_object_key(self._domain.name, self._name, key)
        deleted = self._redis().delete(obj_key)
        if deleted == 0:
            raise KeyError(f"{key!r} not found in namespace {self._name!r}")

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
