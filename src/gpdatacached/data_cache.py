"""GPDataCache: a facade managing GPDCDomain instances and GPDC-URI access."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from gpconfig import ConfigNotFoundError

from .exceptions import ObjectNotFoundByURIError

if TYPE_CHECKING:
    from gpconfig import GPConfigFolder

    from .domain import GPDCDomain
    from .object import GPDCDataObject

logger = logging.getLogger(__name__)


class GPDataCache:
    """Manage GPDCDomain instances from a GPConfigFolder and resolve GPDC URIs.

    Domains are constructed lazily via gpconfig's object factory
    (``folder.get_object(name)``) and cached. Construction happens on first
    ``get_domain`` access; ``__init__`` does not create any domain.

    The cache is a snapshot: config files edited after construction are NOT
    re-read. Rebuild the GPDataCache to pick up changes.

    Use as a context manager to ensure all cached domains are closed::

        with GPDataCache(folder) as cache:
            ...
    """

    def __init__(self, folder: "GPConfigFolder") -> None:
        self._folder = folder
        self._domains: dict[str, GPDCDomain] = {}
        self._closed = False

    def __contains__(self, domain_name: object) -> bool:
        """Return True iff ``domain_name`` has a config in this folder.

        Pure config-snapshot check via ``folder.list_configs()``: does not
        touch Redis and does not construct a GPDCDomain.
        """
        if not isinstance(domain_name, str):
            return False
        return domain_name in self._folder.list_configs()

    def get_domain(self, domain_name: str) -> "GPDCDomain":
        """Return the cached GPDCDomain for ``domain_name``, creating it lazily.

        Propagates gpconfig's ``ConfigNotFoundError`` if the domain has no
        config file in this folder. GPDataCache does not create domains
        without a config (use GPDCDomain directly for ad-hoc domains).
        """
        self._check_open()
        if domain_name not in self._domains:
            self._domains[domain_name] = self._folder.get_object(domain_name)
        return self._domains[domain_name]

    def get_object_by_uri(self, uri: str) -> "GPDCDataObject":
        """Resolve a GPDC URI to a GPDCDataObject.

        ``uri`` must be ``gpdc://{domain}/{namespace}/{name}`` optionally
        followed by path segments that descend into nested containers via
        ``__getitem__`` (e.g. ``gpdc://d/ns/obj/key/0``).

        Always returns a GPDCDataObject — never a native value. A path that
        terminates on a scalar value raises ObjectNotFoundByURIError.

        Raises ObjectNotFoundByURIError for every failure mode: malformed
        URI, missing domain config, missing namespace, missing root object,
        broken path, scalar terminal.
        """
        self._check_open()
        domain_name, ns_name, obj_name, path_parts = self._parse_uri(uri)

        # Domain: wrap ConfigNotFoundError into the URI exception contract.
        try:
            domain = self.get_domain(domain_name)
        except ConfigNotFoundError as e:
            raise ObjectNotFoundByURIError(uri, f"domain {domain_name!r} not configured") from e

        # Namespace: get_namespace raises KeyError if absent.
        try:
            ns = domain.get_namespace(ns_name)
        except KeyError:
            raise ObjectNotFoundByURIError(
                uri, f"namespace {ns_name!r} not found in domain {domain_name!r}"
            )

        # Root object: get_object returns the object itself (not a value).
        try:
            root = ns.get_object(obj_name)
        except KeyError:
            raise ObjectNotFoundByURIError(
                uri, f"object {obj_name!r} not found in namespace {ns_name!r}"
            )

        # Path traversal is added in a later task; with no path_parts the
        # root IS the terminal. A root scalar object is itself a GPDCDataObject,
        # so it passes the terminal check.
        return self._traverse(root, path_parts, uri)

    @staticmethod
    def _parse_uri(uri: str) -> tuple[str, str, str, list[str]]:
        """Split a GPDC URI into (domain, namespace, name, path_parts)."""
        if not uri.startswith("gpdc://"):
            raise ObjectNotFoundByURIError(uri, "URI must start with 'gpdc://'")
        rest = uri[len("gpdc://"):]
        parts = rest.split("/")
        if len(parts) < 3:
            raise ObjectNotFoundByURIError(
                uri, "URI must have at least domain/namespace/name segments"
            )
        if any(p == "" for p in parts):
            raise ObjectNotFoundByURIError(uri, "URI contains an empty segment")
        domain, ns_name, obj_name = parts[0], parts[1], parts[2]
        path_parts = parts[3:]
        return domain, ns_name, obj_name, path_parts

    @staticmethod
    def _traverse(root, path_parts, uri):
        """Descend from root along path_parts. Fully implemented in Task 6."""
        # Placeholder: Task 6 fills in real traversal. For now, only the
        # no-path case (path_parts empty) is valid and returns root.
        if path_parts:
            raise ObjectNotFoundByURIError(uri, "path traversal not yet supported")
        return root

    def close(self) -> None:
        """Close every cached GPDCDomain. Idempotent."""
        if self._closed:
            return
        for name, domain in self._domains.items():
            try:
                domain.close()
            except Exception:
                logger.warning("Error closing domain %r during GPDataCache.close()", name, exc_info=True)
        self._domains.clear()
        self._closed = True

    def __enter__(self) -> "GPDataCache":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError("GPDataCache is closed")
