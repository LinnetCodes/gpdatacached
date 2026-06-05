from __future__ import annotations

from typing import ClassVar, Optional

from gpconfig import GPConfig
from pydantic import Field, field_validator, model_validator


class GPDCNamespaceConfig(GPConfig):
    """Configuration for a GPDC namespace."""

    cfg_class_name: ClassVar[str] = "GPDCNamespaceConfig"

    default_cache_mode: Optional[str] = None
    default_ttl_seconds: Optional[int] = None

    @field_validator("default_cache_mode")
    @classmethod
    def _validate_cache_mode(cls, v: str | None) -> str | None:
        if v is not None and v not in ("permanent", "ttl", "sliding"):
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
        if (
            self.default_cache_mode in ("ttl", "sliding")
            and self.default_ttl_seconds is not None
            and self.default_ttl_seconds <= 0
        ):
            raise ValueError(
                f"default_cache_mode='{self.default_cache_mode}' requires "
                f"default_ttl_seconds > 0, got {self.default_ttl_seconds}"
            )
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

    @field_validator("default_cache_mode")
    @classmethod
    def _validate_cache_mode(cls, v: str) -> str:
        if v not in ("permanent", "ttl", "sliding"):
            raise ValueError(f"Invalid default_cache_mode: {v!r}")
        return v

    @field_validator("default_ttl_seconds")
    @classmethod
    def _validate_ttl(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"Invalid default_ttl_seconds: {v!r}")
        return v

    @model_validator(mode="after")
    def _validate_ttl_mode_consistency(self) -> GPDCDomainConfig:
        if self.default_cache_mode in ("ttl", "sliding") and self.default_ttl_seconds <= 0:
            raise ValueError(
                f"default_cache_mode='{self.default_cache_mode}' requires "
                f"default_ttl_seconds > 0, got {self.default_ttl_seconds}"
            )
        return self
