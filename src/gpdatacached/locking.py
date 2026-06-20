"""Distributed locking primitives for gpdatacached.

See ``dev_docs/2026-06-21-cross-process-locking-design.md`` for the design.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import redis

logger = logging.getLogger(__name__)


# Lua script for atomic release.
# Verifies token, deletes lock, wakes one waiter via RPUSH, sets short TTL on the
# notify list so it self-cleans if no waiter consumes the wake-up.
_RELEASE_SCRIPT_BODY = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    redis.call('DEL', KEYS[1])
    redis.call('RPUSH', KEYS[2], '1')
    redis.call('EXPIRE', KEYS[2], 5)
    return 1
else
    return 0
end
"""


class HeldLock:
    """Handle for a lock that has been successfully acquired.

    Call ``release()`` to release the lock, or use the instance as a context
    manager (recommended). Idempotent: calling ``release()`` more than once is
    safe.

    If the lock was lost before release (TTL expired or stolen by another
    process after TTL expiry), ``release()`` logs a warning and does not raise
    — the caller has no recovery path.
    """

    __slots__ = ("_r", "_lock_key", "_notify_key", "_token", "_released", "_script")

    def __init__(
        self,
        r: "redis.Redis",
        lock_key: str,
        notify_key: str,
        token: str,
    ) -> None:
        self._r = r
        self._lock_key = lock_key
        self._notify_key = notify_key
        self._token = token
        self._released = False
        # register_script returns a callable that handles EVALSHA/EVAL transparently.
        # Cached per Redis instance by redis-py.
        self._script = r.register_script(_RELEASE_SCRIPT_BODY)

    @property
    def lock_key(self) -> str:
        return self._lock_key

    @property
    def token(self) -> str:
        return self._token

    def release(self) -> None:
        """Release the lock. Idempotent. Logs a warning on token mismatch."""
        if self._released:
            return
        self._released = True
        ok = self._script(keys=[self._lock_key, self._notify_key], args=[self._token])
        if ok == 0:
            logger.warning(
                "Lock %s lost before release (TTL expired or token mismatch)",
                self._lock_key,
            )

    def __enter__(self) -> "HeldLock":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.release()
        return False
