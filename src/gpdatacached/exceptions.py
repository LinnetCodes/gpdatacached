from __future__ import annotations


class GPDCError(Exception):
    """Base exception for all gpdatacached errors."""


class ObjectGoneError(GPDCError):
    def __init__(self, object_uri: str) -> None:
        self.object_uri = object_uri
        super().__init__(f"Object {object_uri!r} no longer exists in Redis")


class CanonicalObjectReferencedError(GPDCError):
    def __init__(
        self,
        canonical_name: str,
        canonical_uri: str,
        referrers: list[str],
    ) -> None:
        self.canonical_name = canonical_name
        self.canonical_uri = canonical_uri
        self.referrers = referrers
        names = ", ".join(repr(r) for r in referrers)
        super().__init__(
            f"Cannot delete canonical object {canonical_name!r} ({canonical_uri}) "
            f"because it is still referenced by: [{names}]"
        )


class LifecyclePermissionError(GPDCError):
    """Raised when attempting to modify lifecycle of an object not owned by the caller."""

    def __init__(self, canonical_name: str, owner_name: str) -> None:
        self.canonical_name = canonical_name
        self.owner_name = owner_name
        super().__init__(
            f"Cannot modify lifecycle of {canonical_name!r}: "
            f"owned by {owner_name!r}. "
            f"Call take_ownership() first to gain lifecycle management rights."
        )


class CyclicReferenceError(GPDCError):
    """Raised when a cyclic container reference is detected during encoding."""

    def __init__(self, type_name: str) -> None:
        self.type_name = type_name
        super().__init__(
            f"Cannot encode cyclic reference involving {type_name!r}: "
            f"a container references itself directly or transitively. "
            f"Break the cycle before storing, or store the shared container "
            f"separately and reference it by name."
        )


class GPDCLockError(GPDCError):
    """Base class for all lock-acquisition/ownership errors."""


class LockAcquisitionTimeout(GPDCLockError):
    """Raised when ``obj.lock()`` cannot acquire the lock within ``timeout``."""

    def __init__(self, lock_key: str, timeout: float) -> None:
        self.lock_key = lock_key
        self.timeout = timeout
        super().__init__(f"Acquiring lock {lock_key!r} timed out after {timeout}s")


class LockOwnershipError(GPDCLockError):
    """Raised when releasing a lock whose Redis token no longer matches.

    Internal: ``HeldLock.release()`` swallows this case (logs a warning) instead
    of raising, because the caller has no recovery path. The exception class is
    kept for callers that wrap release logic themselves.
    """


class GPDCLockConfigInconsistentError(GPDCError):
    """Raised at GPDCDomain construction when the lock config in the explicit
    ``GPDCDomainConfig`` conflicts with values already stored in Redis domain
    meta.

    Note: extends ``GPDCError`` directly (not ``GPDCLockError``) because this
    is a configuration error surfaced before any lock acquisition is attempted.
    """

    def __init__(
        self,
        domain: str,
        conflicts: dict[str, tuple[str, str]],
        legal_values: dict[str, str],
    ) -> None:
        self.domain = domain
        self.conflicts = conflicts
        self.legal_values = legal_values

        lines = [f"Lock config for domain {domain!r} inconsistent with Redis:"]
        for field, (redis_val, config_val) in conflicts.items():
            lines.append(f"  conflict: {field}: redis={redis_val!r}, your config={config_val!r}")
        lines.append("Legal values currently in Redis domain meta:")
        if legal_values:
            for field, val in legal_values.items():
                lines.append(f"  {field} = {val}")
        else:
            lines.append("  (none)")
        lines.append(
            "To change these values, call "
            "gpdatacached.reset_domain_config(name, config) "
            "before constructing GPDCDomain."
        )
        super().__init__("\n".join(lines))


class ObjectNotFoundByURIError(GPDCError):
    """Raised when a GPDC URI cannot be resolved to a GPDCDataObject.

    Covers all failure modes of GPDataCache.get_object_by_uri / has_uri:
    missing domain config, missing namespace, missing root object, broken
    path traversal, scalar terminal (path traversal hit an inline scalar
    value), and malformed URI syntax.
    """

    def __init__(self, uri: str, reason: str = "") -> None:
        self.uri = uri
        self.reason = reason
        msg = f"Cannot resolve GPDC URI {uri!r}"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
