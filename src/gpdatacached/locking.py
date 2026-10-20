"""Distributed locking primitives for gpdatacached.

See ``dev_docs/2026-06-21-cross-process-locking-design.md`` for the design.
"""

from __future__ import annotations

import contextlib
import logging
import time
import uuid
from collections.abc import Iterator
from typing import TYPE_CHECKING

from .exceptions import LockAcquisitionTimeout

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

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        # Never suppress exceptions: returning None (≡ False for the with
        # protocol) — annotated None because mypy's exit-return check rejects
        # `-> bool` on an __exit__ that can only ever return False.
        self.release()


def try_lock(
    r: "redis.Redis",
    lock_key: str,
    notify_key: str,
    *,
    ttl: int,
) -> HeldLock | None:
    """Non-blocking lock attempt.

    Returns a ``HeldLock`` on success, or ``None`` if the lock is currently held.
    """
    token = uuid.uuid4().hex
    if r.set(lock_key, token, nx=True, ex=ttl):
        return HeldLock(r, lock_key, notify_key, token)
    return None


def acquire(
    r: "redis.Redis",
    lock_key: str,
    notify_key: str,
    *,
    ttl: int,
    timeout: float,
) -> HeldLock:
    """Block until the lock is acquired or ``timeout`` elapses.

    Algorithm: ``SET NX EX`` fast-path; on failure, ``BLPOP`` on the notify
    queue; retry on wake or BLPOP timeout.

    Raises:
        LockAcquisitionTimeout: if the lock cannot be acquired within ``timeout``.
    """
    token = uuid.uuid4().hex
    deadline = time.monotonic() + timeout

    while True:
        if r.set(lock_key, token, nx=True, ex=ttl):
            return HeldLock(r, lock_key, notify_key, token)

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LockAcquisitionTimeout(lock_key=lock_key, timeout=timeout)

        # BLPOP timeout is in seconds (float accepted in Redis 6+).
        # Cap at min(remaining, ttl) so we retry SET NX after the lock's TTL
        # expires, even if no release notification arrives (e.g. holder crashed
        # without releasing). Per the design, a waiter should recover within
        # ~ttl of a holder crash, not wait the full acquire timeout.
        blpop_timeout = min(remaining, ttl)
        r.blpop(notify_key, timeout=blpop_timeout)


@contextlib.contextmanager
def lock_context_manager(
    r: "redis.Redis",
    lock_key: str,
    notify_key: str,
    *,
    ttl: int,
    timeout: float,
) -> "Iterator[HeldLock]":
    """Context manager wrapper around ``acquire`` + ``release``."""
    held = acquire(r, lock_key, notify_key, ttl=ttl, timeout=timeout)
    try:
        yield held
    finally:
        held.release()
