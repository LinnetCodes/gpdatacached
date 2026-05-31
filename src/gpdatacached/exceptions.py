from __future__ import annotations


class ObjectGoneError(Exception):
    def __init__(self, object_uri: str) -> None:
        self.object_uri = object_uri
        super().__init__(f"Object {object_uri!r} no longer exists in Redis")


class CanonicalObjectReferencedError(Exception):
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
            f"Cannot delete canonical object {canonical_name!r} because it is still referenced by: [{names}]"
        )
