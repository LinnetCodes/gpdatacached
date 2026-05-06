from __future__ import annotations

from typing import ClassVar, Optional

from gpconfig import GPConfig


class GPDCNamespaceConfig(GPConfig):
    """Configuration for a GPDC namespace."""

    cfg_class_name: ClassVar[str] = "GPDCNamespaceConfig"

    default_cache_mode: Optional[str] = None
    default_ttl_seconds: Optional[int] = None


class GPDCDomainConfig(GPConfig):
    """Configuration for a GPDC domain."""

    cfg_class_name: ClassVar[str] = "GPDCDomainConfig"

    # Redis connection parameters
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = ""
    redis_max_retries: int = 3
    redis_retry_delay: float = 1.0

    # Cache policy defaults (creator role)
    default_cache_mode: str = "permanent"
    default_ttl_seconds: int = 0

    # GC (reserved for future phases)
    gc_enabled: bool = True
    gc_interval_seconds: int = 300

    # Pre-declared namespaces
    namespaces: dict[str, GPDCNamespaceConfig] = {}
