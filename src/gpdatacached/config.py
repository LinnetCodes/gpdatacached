from __future__ import annotations

from typing import ClassVar, Optional

from gpconfig import GPConfig
from pydantic import Field, field_validator, model_validator

from .lifecycle import VALID_CACHE_MODES, validate_cache_mode_and_ttl

# Minimum allowed value for GPDCDomainConfig.gc_interval_seconds. A smaller
# interval would busy-loop the background GC thread (Event.wait(0/negative)
# returns False immediately) and flood Redis with repeated SCAN/lock activity
# (CQR-2026-07-24 perf.01). 30s is a deliberately conservative floor: GC is a
# safety net for orphaned anonymous objects and expired TTL keys, not a
# latency-sensitive path, so a half-minute minimum keeps Redis pressure low
# while still allowing reasonably frequent collection.
MIN_GC_INTERVAL_SECONDS = 30


class GPDCNamespaceConfig(GPConfig):
    """Configuration for a GPDC namespace."""

    cfg_class_name: ClassVar[str] = "GPDCNamespaceConfig"

    default_cache_mode: Optional[str] = None
    default_ttl_seconds: Optional[int] = None

    @field_validator("default_cache_mode")
    @classmethod
    def _validate_cache_mode(cls, v: str | None) -> str | None:
        if v is not None and v not in VALID_CACHE_MODES:
            raise ValueError(f"Invalid default_cache_mode: {v!r}")
        return v

    @field_validator("default_ttl_seconds")
    @classmethod
    def _validate_ttl(cls, v: int | None) -> int | None:
        if v is not None and v < 0:
            raise ValueError(f"Invalid default_ttl_seconds: {v!r}")
        return v

    @model_validator(mode="after")
    def _validate_ttl_mode_consistency(self) -> GPDCNamespaceConfig:
        if self.default_cache_mode is not None and self.default_ttl_seconds is not None:
            validate_cache_mode_and_ttl(self.default_cache_mode, self.default_ttl_seconds)
        return self


class GPDCDomainConfig(GPConfig):
    """Configuration for a GPDC domain."""

    cfg_class_name: ClassVar[str] = "GPDCDomainConfig"

    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = ""

    default_cache_mode: str = "permanent"
    default_ttl_seconds: int = 0

    gc_enabled: bool = False
    gc_interval_seconds: int = 300

    namespaces: dict[str, GPDCNamespaceConfig] = Field(default_factory=dict)

    # Lock configuration (see dev_docs/2026-06-21-cross-process-locking-design.md).
    default_lock_ttl_seconds: int = 30
    default_lock_acquire_timeout_seconds: int = 30
    gc_lock_ttl_seconds: int = 30
    gc_lock_wait_seconds: int = 5

    @field_validator("default_cache_mode")
    @classmethod
    def _validate_cache_mode(cls, v: str) -> str:
        if v not in VALID_CACHE_MODES:
            raise ValueError(f"Invalid default_cache_mode: {v!r}")
        return v

    @field_validator("default_ttl_seconds")
    @classmethod
    def _validate_ttl(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"Invalid default_ttl_seconds: {v!r}")
        return v

    @field_validator(
        "default_lock_ttl_seconds",
        "gc_lock_ttl_seconds",
    )
    @classmethod
    def _validate_lock_ttl_fields(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"Lock TTL must be >= 1 (Redis SET NX EX requires positive ttl), got {v!r}")
        return v

    @field_validator(
        "default_lock_acquire_timeout_seconds",
        "gc_lock_wait_seconds",
    )
    @classmethod
    def _validate_lock_wait_fields(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"Lock wait/timeout must be >= 0 (0 = non-blocking), got {v!r}")
        return v

    @field_validator("gc_interval_seconds")
    @classmethod
    def _validate_gc_interval(cls, v: int) -> int:
        # CQR-2026-07-24 perf.01: an interval <= 0 makes the background GC thread
        # busy-loop (threading.Event.wait with a 0/negative timeout returns False
        # immediately), saturating a CPU core and flooding Redis with SCAN/lock
        # activity. Floor it at MIN_GC_INTERVAL_SECONDS. (Note: a negative timeout
        # does NOT raise on CPython — it behaves like 0 — so this guard also closes
        # the negative case the report claimed would "terminate the daemon path".)
        if v < MIN_GC_INTERVAL_SECONDS:
            raise ValueError(
                f"gc_interval_seconds must be >= {MIN_GC_INTERVAL_SECONDS} "
                f"(got {v!r}); smaller values busy-loop the GC thread and flood Redis."
            )
        return v

    @model_validator(mode="after")
    def _validate_ttl_mode_consistency(self) -> GPDCDomainConfig:
        validate_cache_mode_and_ttl(self.default_cache_mode, self.default_ttl_seconds)
        return self
