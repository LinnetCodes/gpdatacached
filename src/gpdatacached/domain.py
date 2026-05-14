# src/gpdatacached/domain.py
from __future__ import annotations

import threading

import redis
from gpconfig import GPConfigurable

from .config import GPDCDomainConfig
from .keys import (
    GLOBAL_META_KEY,
    GLOBAL_DOMAINS_KEY,
    make_domain_meta_key,
    make_domain_namespaces_key,
    validate_domain_name,
)

GPDC_VERSION = "0.1.0"


class GPDCDomain(GPConfigurable):
    """GPDC domain: a Redis connection pool and the configuration root."""

    def __init__(self, name: str, config: GPDCDomainConfig) -> None:
        super().__init__(config)
        validate_domain_name(name)
        self._name = name
        self._redis: redis.Redis | None = None
        self._namespaces: dict = {}
        self._gc_thread: threading.Thread | None = None
        self._gc_stop_event = threading.Event()
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
            self._redis.hset(GLOBAL_META_KEY, "version", GPDC_VERSION)
        elif existing != GPDC_VERSION:
            raise RuntimeError(f"Incompatible GPDC version in Redis: {existing!r} (expected {GPDC_VERSION!r})")

    def _init_domain_meta(self) -> None:
        """Initialize domain metadata in Redis."""
        cfg = self._config
        meta_key = make_domain_meta_key(self._name)
        existing = self._redis.hgetall(meta_key)
        if not existing:
            self._redis.hset(
                meta_key,
                mapping={
                    "default_cache_mode": cfg.default_cache_mode,
                    "default_ttl_seconds": str(cfg.default_ttl_seconds),
                },
            )
        # Always register this domain in the global domains set
        self._redis.sadd(GLOBAL_DOMAINS_KEY, self._name)

    def _init_predefined_namespaces(self) -> None:
        """Create predefined namespaces declared in config, applying per-namespace settings.

        Delegates config-priority logic to GPDCNamespace.__init__: if the
        namespace does not yet exist in Redis the per-namespace config is used;
        if it already exists the Redis metadata is left untouched.
        """
        from .config import GPDCNamespaceConfig
        from .namespace import GPDCNamespace

        for ns_name, ns_cfg_dict in self._config.namespaces.items():
            if ns_name not in self._namespaces:
                ns_config = GPDCNamespaceConfig(
                    default_cache_mode=ns_cfg_dict.get("default_cache_mode"),
                    default_ttl_seconds=ns_cfg_dict.get("default_ttl_seconds"),
                )
                self._namespaces[ns_name] = GPDCNamespace(ns_name, self, config=ns_config)

    @property
    def name(self) -> str:
        return self._name

    @property
    def redis(self) -> redis.Redis:
        return self._redis

    def get_namespace(self, name: str):
        from .namespace import GPDCNamespace

        if name not in self._namespaces:
            self._namespaces[name] = GPDCNamespace(name, self)
        return self._namespaces[name]

    def __contains__(self, name: object) -> bool:
        if not isinstance(name, str):
            return False
        return self._redis.sismember(make_domain_namespaces_key(self._name), name)

    def __iter__(self):
        return iter(self._redis.smembers(make_domain_namespaces_key(self._name)))

    def __len__(self) -> int:
        return self._redis.scard(make_domain_namespaces_key(self._name))

    def __getitem__(self, name: str):
        if not self._redis.sismember(make_domain_namespaces_key(self._name), name):
            raise KeyError(name)
        if name not in self._namespaces:
            from .namespace import GPDCNamespace

            self._namespaces[name] = GPDCNamespace(name, self)
        return self._namespaces[name]

    def get_default_cache_mode(self) -> str:
        """Effective default_cache_mode for this domain."""
        return self._config.default_cache_mode

    def get_default_ttl_seconds(self) -> int:
        """Effective default_ttl_seconds for this domain."""
        return self._config.default_ttl_seconds

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
            except Exception:
                pass

    def run_gc(self, namespace: str | None = None) -> int:
        """Run GC manually. If namespace is None, run for all namespaces."""
        from .gc import run_gc_for_namespace

        total = 0
        if namespace:
            total += run_gc_for_namespace(self._redis, self._name, namespace)
        else:
            for ns_name in self:
                total += run_gc_for_namespace(self._redis, self._name, ns_name)
        return total

    def close(self) -> None:
        """Stop GC thread and release resources."""
        self._gc_stop_event.set()
        if self._gc_thread and self._gc_thread.is_alive():
            self._gc_thread.join(timeout=5)
        self._gc_thread = None
        if self._redis:
            self._redis.close()
