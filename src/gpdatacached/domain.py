# src/gpdatacached/domain.py
from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

import redis
from gpconfig import GPConfigurable

from .config import GPDCDomainConfig
from .keys import (
    GLOBAL_META_KEY,
    GLOBAL_DOMAINS_KEY,
    make_domain_meta_key,
    make_domain_namespaces_key,
    make_namespace_meta_key,
    unescape_domain_name,
    validate_domain_name,
)

if TYPE_CHECKING:
    from .config import GPDCDomainConfig
    from .namespace import GPDCNamespace

logger = logging.getLogger(__name__)

# Wire protocol version (Major.Minor), changes only when Redis data layout
# becomes incompatible.  Do not confuse with the library __version__.
GPDC_PROTOCOL_VERSION = "1.0"


class GPDCDomain(GPConfigurable):
    """GPDC domain: a Redis connection pool and the configuration root."""

    def __init__(self, config: GPDCDomainConfig, name: str | None = None) -> None:
        # Signature changed in 0.2.0: previously GPDCDomain(name, config).
        # The first argument is now the config; ``name`` is optional because a
        # config loaded from file already carries its name (GPConfig.name == the
        # YAML filename stem). An old-style call passes a str as the first arg,
        # which we detect up front so the failure is an explicit, actionable
        # error rather than a confusing AttributeError deep in _connect().
        if isinstance(config, str):
            raise TypeError(
                f"GPDCDomain() signature changed in 0.2.0: the first argument must be a "
                f"GPDCDomainConfig, got a str ({config!r}). Use GPDCDomain(config, name=...) "
                f"instead of the old GPDCDomain(name, config)."
            )

        super().__init__(config)

        # Resolve the domain name. config.name is populated when the config was
        # loaded from a file (== filename stem); it is "" for a programmatically
        # constructed config. Prefer config.name, fall back to the explicit name
        # argument, and reject a conflict between the two.
        #
        # A '@' in a stem-derived config.name is the filename escape for '.'
        # (see keys.DOMAIN_DOT_ESCAPE): gpconfig's dot-notation cannot address
        # dotted filenames, so domain_name@com.yaml spells the domain
        # "domain_name.com". Restore it here — before the mismatch check and
        # validation — so both gpconfig's object factory
        # (folder.get_object("domain_name@com")) and GPDataCache construct the
        # identically-named domain. The restore only applies to the stem-derived
        # value: '@' cannot occur in a legal domain name, so this never alters
        # an already-valid name, and an explicit name= containing '@' is still
        # rejected loudly by validate_domain_name below.
        config_name = config.name
        if config_name:
            config_name = unescape_domain_name(config_name)
        if name is not None and config_name:
            if name != config_name:
                raise ValueError(
                    f"Domain name mismatch: explicit name={name!r} conflicts with "
                    f"config.name={config_name!r}. Provide only one, or make them agree."
                )
            resolved = name
        elif config_name:
            resolved = config_name
        elif name is not None:
            resolved = name
        else:
            raise ValueError(
                "GPDCDomain requires a name: pass name=..., or load config from a file "
                "(config.name is then derived from the filename)."
            )

        validate_domain_name(resolved)
        self._name = resolved
        self._namespaces: dict[str, GPDCNamespace] = {}
        self._gc_thread: threading.Thread | None = None
        self._gc_stop_event = threading.Event()
        self._closed = False
        self._redis: redis.Redis = None  # type: ignore[assignment]
        self._connect()
        self._start_gc()

    def _connect(self) -> None:
        cfg = self._config
        pool = redis.ConnectionPool(
            host=cfg.redis_host,
            port=cfg.redis_port,
            db=cfg.redis_db,
            password=cfg.redis_password or None,
            decode_responses=True,
        )
        self._redis = redis.Redis(connection_pool=pool)
        self._init_global_meta()
        self._init_domain_meta()
        self._init_predefined_namespaces()

    def _init_global_meta(self) -> None:
        """Initialize or verify global GPDC metadata."""
        existing = self._redis.hget(GLOBAL_META_KEY, "version")
        if existing is None:
            self._redis.hset(GLOBAL_META_KEY, "version", GPDC_PROTOCOL_VERSION)
        elif existing != GPDC_PROTOCOL_VERSION:
            raise RuntimeError(f"Incompatible GPDC version in Redis: {existing!r} (expected {GPDC_PROTOCOL_VERSION!r})")

    def _init_domain_meta(self) -> None:
        """Initialize or verify domain metadata in Redis.

        Legacy fields (``default_cache_mode``, ``default_ttl_seconds``) keep
        their existing silent write-once behaviour: written when the domain is
        new, never compared against config thereafter.

        Lock fields (``default_lock_ttl_seconds``, etc.) follow the
        ``_init_global_meta`` write-or-verify pattern: written when new,
        backfilled if missing, raise ``GPDCLockConfigInconsistentError`` on
        mismatch.
        """
        cfg = self._config
        meta_key = make_domain_meta_key(self._name)
        existing = self._redis.hgetall(meta_key)

        lock_fields = {
            "default_lock_ttl_seconds": str(cfg.default_lock_ttl_seconds),
            "default_lock_acquire_timeout_seconds": str(cfg.default_lock_acquire_timeout_seconds),
            "gc_lock_ttl_seconds": str(cfg.gc_lock_ttl_seconds),
            "gc_lock_wait_seconds": str(cfg.gc_lock_wait_seconds),
        }

        if not existing:
            # New domain: write legacy + lock fields in one shot.
            self._redis.hset(
                meta_key,
                mapping={
                    "default_cache_mode": cfg.default_cache_mode,
                    "default_ttl_seconds": str(cfg.default_ttl_seconds),
                    **lock_fields,
                },
            )
        else:
            # Existing domain.
            # Legacy fields: untouched (silent write-once, matches prior behaviour).
            # Lock fields: write-or-verify.
            to_write: dict[str, str] = {}
            conflicts: dict[str, tuple[str, str]] = {}
            for field, expected in lock_fields.items():
                actual = existing.get(field)
                if actual is None:
                    to_write[field] = expected
                elif actual != expected:
                    conflicts[field] = (actual, expected)

            if conflicts:
                from .exceptions import GPDCLockConfigInconsistentError

                legal_values = {k: existing[k] for k in lock_fields if k in existing}
                raise GPDCLockConfigInconsistentError(
                    domain=self._name,
                    conflicts=conflicts,
                    legal_values=legal_values,
                )

            if to_write:
                self._redis.hset(meta_key, mapping=to_write)

        # Always register this domain in the global domains set.
        self._redis.sadd(GLOBAL_DOMAINS_KEY, self._name)

    def _init_predefined_namespaces(self) -> None:
        """Create predefined namespaces declared in config, applying per-namespace settings.

        Delegates config-priority logic to GPDCNamespace.__init__: if the
        namespace does not yet exist in Redis the per-namespace config is used;
        if it already exists the Redis metadata is left untouched.
        """
        from .namespace import GPDCNamespace

        for ns_name, ns_config in self._config.namespaces.items():
            if ns_name not in self._namespaces:
                self._namespaces[ns_name] = GPDCNamespace(ns_name, self, config=ns_config)

    @property
    def name(self) -> str:
        return self._name

    def __repr__(self) -> str:
        return f"GPDCDomain({self._name!r})"

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError(f"GPDCDomain {self._name!r} is closed")

    @property
    def redis(self) -> redis.Redis:
        self._check_open()
        return self._redis

    def ensure_namespace(self, name: str):
        self._check_open()
        from .namespace import GPDCNamespace

        if name not in self._namespaces:
            self._namespaces[name] = GPDCNamespace(name, self)
        return self._namespaces[name]

    def get_namespace(self, name: str):
        return self[name]

    def __contains__(self, name: object) -> bool:
        self._check_open()
        if not isinstance(name, str):
            return False
        return self._redis.sismember(make_domain_namespaces_key(self._name), name)

    def __iter__(self):
        self._check_open()
        return iter(self._redis.smembers(make_domain_namespaces_key(self._name)))

    def __len__(self) -> int:
        self._check_open()
        return self._redis.scard(make_domain_namespaces_key(self._name))

    def __getitem__(self, name: str):
        self._check_open()
        if not self._redis.sismember(make_domain_namespaces_key(self._name), name):
            raise KeyError(name)
        if name not in self._namespaces:
            from .namespace import GPDCNamespace

            self._namespaces[name] = GPDCNamespace(name, self)
        return self._namespaces[name]

    def get_default_cache_mode(self) -> str:
        """Effective default_cache_mode for this domain."""
        meta_key = make_domain_meta_key(self._name)
        val = self._redis.hget(meta_key, "default_cache_mode")
        if val:
            return val
        return self._config.default_cache_mode

    def get_default_ttl_seconds(self) -> int:
        """Effective default_ttl_seconds for this domain."""
        meta_key = make_domain_meta_key(self._name)
        raw = self._redis.hget(meta_key, "default_ttl_seconds")
        if raw:
            return int(raw)
        return self._config.default_ttl_seconds

    def get_default_lock_ttl_seconds(self) -> int:
        """Effective ``default_lock_ttl_seconds`` for this domain."""
        meta_key = make_domain_meta_key(self._name)
        raw = self._redis.hget(meta_key, "default_lock_ttl_seconds")
        return int(raw) if raw else self._config.default_lock_ttl_seconds

    def get_default_lock_acquire_timeout_seconds(self) -> int:
        """Effective ``default_lock_acquire_timeout_seconds`` for this domain."""
        meta_key = make_domain_meta_key(self._name)
        raw = self._redis.hget(meta_key, "default_lock_acquire_timeout_seconds")
        return int(raw) if raw else self._config.default_lock_acquire_timeout_seconds

    def get_gc_lock_ttl_seconds(self) -> int:
        """Effective ``gc_lock_ttl_seconds`` for this domain."""
        meta_key = make_domain_meta_key(self._name)
        raw = self._redis.hget(meta_key, "gc_lock_ttl_seconds")
        return int(raw) if raw else self._config.gc_lock_ttl_seconds

    def get_gc_lock_wait_seconds(self) -> int:
        """Effective ``gc_lock_wait_seconds`` for this domain."""
        meta_key = make_domain_meta_key(self._name)
        raw = self._redis.hget(meta_key, "gc_lock_wait_seconds")
        return int(raw) if raw else self._config.gc_lock_wait_seconds

    def _start_gc(self) -> None:
        cfg = self._config
        if not cfg.gc_enabled:
            return
        self._gc_thread = threading.Thread(target=self._gc_loop, daemon=True, name=f"gpdc-gc-{self._name}")
        self._gc_thread.start()

    def _gc_loop(self) -> None:
        from .gc import run_gc_for_namespace

        interval = self._config.gc_interval_seconds
        while not self._gc_stop_event.wait(timeout=interval):
            try:
                for ns_name in self:
                    run_gc_for_namespace(self._redis, self._name, ns_name)
            except Exception as e:
                logger.warning("GPDC GC error in domain %s: %s", self._name, e)

    def run_gc(self, namespace: str | None = None) -> int:
        """Run GC manually. If namespace is None, run for all namespaces."""
        self._check_open()
        from .gc import run_gc_for_namespace

        total = 0
        if namespace:
            total += run_gc_for_namespace(self._redis, self._name, namespace)
        else:
            for ns_name in self:
                total += run_gc_for_namespace(self._redis, self._name, ns_name)
        return total

    def delete_namespace(self, name: str) -> None:
        self._check_open()
        ns_key = make_domain_namespaces_key(self._name)
        if not self._redis.sismember(ns_key, name):
            raise KeyError(name)
        pattern = f"gpdc:{self._name}:{name}:*"
        cursor = 0
        while True:
            cursor, keys = self._redis.scan(cursor=cursor, match=pattern, count=100)
            if keys:
                self._redis.delete(*keys)
            if cursor == 0:
                break
        self._redis.delete(make_namespace_meta_key(self._name, name))
        self._redis.srem(ns_key, name)
        self._namespaces.pop(name, None)
        logger.info("Deleted namespace %r from domain %r", name, self._name)

    def destroy(self) -> None:
        self._check_open()
        ns_key = make_domain_namespaces_key(self._name)
        for ns_name in list(self._redis.smembers(ns_key)):
            self.delete_namespace(ns_name)
        self._redis.delete(make_domain_meta_key(self._name))
        self._redis.delete(ns_key)
        self._redis.srem(GLOBAL_DOMAINS_KEY, self._name)
        self._namespaces.clear()
        logger.info("Destroyed domain %r", self._name)
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._gc_stop_event.set()
        if self._gc_thread and self._gc_thread.is_alive():
            self._gc_thread.join(timeout=5)
        self._gc_thread = None
        if self._redis is not None:
            self._redis.close()
            self._redis = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def __del__(self):
        # __del__ must never raise: during interpreter shutdown module globals
        # (incl. `logger`) may have been nulled, and an exception here only adds
        # "Exception ignored" noise. Swallow defensively so auto-close stays silent.
        try:
            if not getattr(self, "_closed", True):
                logger.warning("GPDCDomain %r was not explicitly closed; auto-closing in __del__", self._name)
                self.close()
        except Exception:
            pass


def reset_domain_config(name: str, config: "GPDCDomainConfig") -> None:
    """Delete the four lock-config fields from the domain's meta hash in Redis.

    Intended to be called BEFORE constructing ``GPDCDomain`` when the user
    wants to change lock config on an existing domain:

        reset_domain_config("mydomain", old_config)
        domain = GPDCDomain(new_config, name="mydomain")

    No-op if the meta hash has no lock fields (HDEL on missing fields).

    Args:
        name: Domain name whose meta should be cleared.
        config: ``GPDCDomainConfig`` carrying Redis connection parameters.
            Only connection fields are read; lock fields on this config are
            ignored.
    """
    pool = redis.ConnectionPool(
        host=config.redis_host,
        port=config.redis_port,
        db=config.redis_db,
        password=config.redis_password or None,
        decode_responses=True,
    )
    r = redis.Redis(connection_pool=pool)
    try:
        meta_key = make_domain_meta_key(name)
        r.hdel(
            meta_key,
            "default_lock_ttl_seconds",
            "default_lock_acquire_timeout_seconds",
            "gc_lock_ttl_seconds",
            "gc_lock_wait_seconds",
        )
    finally:
        r.close()
