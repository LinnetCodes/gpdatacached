"""GPDataCache: a facade managing GPDCDomain instances and GPDC-URI access."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

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
