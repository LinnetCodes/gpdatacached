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
