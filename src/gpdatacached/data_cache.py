"""GPDataCache: a facade managing GPDCDomain instances and GPDC-URI access."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from gpconfig import ConfigNotFoundError

from .exceptions import ObjectNotFoundByURIError
from .keys import (
    escape_domain_name,
    validate_domain_name,
    validate_namespace_name,
    validate_object_name,
)

if TYPE_CHECKING:
    from gpconfig import GPConfigFolder

    from .config import GPDCDomainConfig
    from .domain import GPDCDomain
    from .object import GPDCDataObject

logger = logging.getLogger(__name__)


class GPDataCache:
    """Manage GPDCDomain instances from a GPConfigFolder and resolve GPDC URIs.

    Domain configs are loaded lazily on first ``get_domain`` access and
    cached; ``__init__`` does not create any domain. Domain names may contain
    '.' — gpconfig cannot address dotted filenames, so a '.' in a domain name
    is written as '@' in its config file (``domain_name@com.yaml`` configures
    domain ``"domain_name.com"``; see DOMAIN_DOT_ESCAPE).

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
        # No dotted-stem scan here: gpconfig >= 0.5.1 fail-fasts at manager
        # construction (ConfigFolderError) for any '.yaml' stem or directory
        # name containing '.', so an unaddressable config can never reach a
        # GPDataCache. (0.3.0 shipped a construction-time warning for this;
        # it was removed as unreachable under the dependency floor.)

    def __contains__(self, domain_name: object) -> bool:
        """Return True iff ``domain_name`` has a config in this folder.

        Pure config-snapshot check via ``folder.list_configs()``: does not
        touch Redis and does not construct a GPDCDomain. The name is validated
        and '@'-escaped first, so ``"domain_name.com" in cache`` matches the
        config file ``domain_name@com.yaml``; names that are not legal GPDC
        domain names (including any containing '@') return False.
        """
        if not isinstance(domain_name, str):
            return False
        try:
            validate_domain_name(domain_name)
        except ValueError:
            return False
        return escape_domain_name(domain_name) in self._folder.list_configs()

    def get_domain(self, domain_name: str) -> "GPDCDomain":
        """Return the cached GPDCDomain for ``domain_name``, creating it lazily.

        ``domain_name`` is validated against the GPDC name rules and
        '@'-escaped for the config lookup (see DOMAIN_DOT_ESCAPE), so a
        dotted domain name resolves to its '@'-escaped config file. The
        config is loaded as GPDCDomainConfig and constructed directly —
        GPDCDomain's own name resolution restores the '@'-escaped stem
        (``domain_name@com``) to the real dotted name
        (``domain_name.com``), so the same factory-built path via
        ``folder.get_object("domain_name@com")`` yields an identically
        named domain.

        Raises ValueError for names that are not legal GPDC domain names,
        and gpconfig's ``ConfigNotFoundError`` if the domain has no config
        file in this folder. GPDataCache does not create domains without a
        config (use GPDCDomain directly for ad-hoc domains).
        """
        self._check_open()
        validate_domain_name(domain_name)
        if domain_name not in self._domains:
            config = self._load_domain_config(domain_name)
            # Imported lazily to keep this module decoupled from the
            # package's import order (same rationale as _traverse).
            from .domain import GPDCDomain

            self._domains[domain_name] = GPDCDomain(config)
        return self._domains[domain_name]

    def _load_domain_config(self, domain_name: str) -> "GPDCDomainConfig":
        """Load the domain config for ``domain_name`` (via its '@'-escaped stem).

        The config keeps its raw stem in ``config.name``; GPDCDomain's name
        resolution performs the '@' -> '.' restore. Raises
        ConfigNotFoundError when no config answers to the escaped name, or
        when something other than a domain config does (e.g. a subfolder
        shadows the config file).
        """
        from .config import GPDCDomainConfig

        escaped = escape_domain_name(domain_name)
        config = self._folder.get_config(escaped, config_cls=GPDCDomainConfig)
        if not isinstance(config, GPDCDomainConfig):
            raise ConfigNotFoundError(escaped, f"resolves to {type(config).__name__}, not a GPDCDomainConfig")
        return config

    def get_object_by_uri(self, uri: str) -> "GPDCDataObject":
        """Resolve a GPDC URI to a GPDCDataObject.

        ``uri`` must be ``gpdc://{domain}/{namespace}/{name}`` optionally
        followed by path segments that descend into nested containers via
        ``__getitem__`` (e.g. ``gpdc://d/ns/obj/key/0``).

        Always returns a GPDCDataObject — never a native value. A path that
        terminates on a scalar value raises ObjectNotFoundByURIError.

        Raises ObjectNotFoundByURIError for every failure mode: malformed
        URI, invalid domain/namespace/object name, missing domain config,
        missing namespace, missing root object, broken path, scalar
        terminal.
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
        except KeyError as e:
            raise ObjectNotFoundByURIError(uri, f"namespace {ns_name!r} not found in domain {domain_name!r}") from e

        # Root object: get_object returns the object itself (not a value).
        try:
            root = ns.get_object(obj_name)
        except KeyError as e:
            raise ObjectNotFoundByURIError(uri, f"object {obj_name!r} not found in namespace {ns_name!r}") from e

        # _traverse descends path_parts via __getitem__ (int-coercing ListObject
        # segments) and enforces the object-only terminal contract; with no
        # path_parts the root itself is the terminal.
        return self._traverse(root, path_parts, uri)

    def has_uri(self, uri: str) -> bool:
        """Return True iff ``get_object_by_uri(uri)`` would succeed.

        Returns False for every ObjectNotFoundByURIError failure mode
        (missing domain/namespace/object, broken path, scalar terminal,
        malformed URI). Infrastructure errors (e.g. Redis connection
        failures) propagate — they are not "the URI doesn't exist".
        """
        self._check_open()
        try:
            self.get_object_by_uri(uri)
        except ObjectNotFoundByURIError:
            return False
        return True

    @staticmethod
    def _parse_uri(uri: str) -> tuple[str, str, str, list[str]]:
        """Split a GPDC URI into (domain, namespace, name, path_parts)."""
        if not uri.startswith("gpdc://"):
            raise ObjectNotFoundByURIError(uri, "URI must start with 'gpdc://'")
        rest = uri[len("gpdc://") :]
        parts = rest.split("/")
        if len(parts) < 3:
            raise ObjectNotFoundByURIError(uri, "URI must have at least domain/namespace/name segments")
        if any(p == "" for p in parts):
            raise ObjectNotFoundByURIError(uri, "URI contains an empty segment")
        domain, ns_name, obj_name = parts[0], parts[1], parts[2]
        # Validate all three locating segments up front so every URI failure
        # mode surfaces as ObjectNotFoundByURIError: a ValueError from any
        # downstream validator (e.g. namespace.get_object validates object
        # names before its existence check) must not leak through
        # get_object_by_uri, and has_uri relies on that contract. Path
        # segments are NOT validated here — they are dict keys / list
        # indices, and invalid ones fail traversal with KeyError/IndexError,
        # which _traverse already wraps.
        for label, segment, validator in (
            ("domain", domain, validate_domain_name),
            ("namespace", ns_name, validate_namespace_name),
            ("object", obj_name, validate_object_name),
        ):
            try:
                validator(segment)
            except ValueError as e:
                raise ObjectNotFoundByURIError(uri, f"invalid {label} name {segment!r}: {e}") from e
        path_parts = parts[3:]
        return domain, ns_name, obj_name, path_parts

    @staticmethod
    def _traverse(root, path_parts, uri):
        """Descend from ``root`` along ``path_parts`` via __getitem__.

        Key coercion is by current object type: ListObject segments must be
        non-negative integers; DictObject/pydantic-container segments stay
        strings. A scalar terminal (or a scalar encountered mid-path) raises
        ObjectNotFoundByURIError — get_object_by_uri returns objects only.
        """
        # Imported lazily inside the method rather than at module top. A
        # module-level import would also work today (data_cache is loaded only
        # after these modules via __init__), but keeping the references local
        # decouples this module from the package's import order so it can be
        # imported standalone if ever needed.
        from .object import GPDCDataObject
        from .builtins.list_object import ListObject

        current = root
        for part in path_parts:
            if not isinstance(current, GPDCDataObject):
                # Defensive: a scalar was reached but path continues.
                raise ObjectNotFoundByURIError(
                    uri, f"path segment {part!r} cannot descend: current node is not a GPDCDataObject"
                )
            if not hasattr(current, "__getitem__"):
                raise ObjectNotFoundByURIError(
                    uri,
                    f"path segment {part!r} cannot descend: {current.canonical_uri} does not support subscript access",
                )
            # Type-based key coercion (decision D9).
            if isinstance(current, ListObject):
                if not part.isdigit():
                    raise ObjectNotFoundByURIError(
                        uri,
                        f"path segment {part!r} invalid for ListObject: must be a non-negative integer",
                    )
                key = int(part)
            else:
                key = part  # DictObject / pydantic container / etc.
            try:
                current = current[key]
            except (KeyError, IndexError, TypeError) as e:
                raise ObjectNotFoundByURIError(
                    uri, f"path segment {part!r} failed at {current.canonical_uri}: {e}"
                ) from e
        # Terminal check: must be a GPDCDataObject, not a scalar value.
        if not isinstance(current, GPDCDataObject):
            raise ObjectNotFoundByURIError(uri, "terminal is a scalar value; get_object_by_uri returns objects only")
        return current

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
