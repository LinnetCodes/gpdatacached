"""Unit tests for the locking primitives."""

from __future__ import annotations

import threading
import time

import pytest
import redis as redis_lib

from gpdatacached.exceptions import LockAcquisitionTimeout
from gpdatacached.locking import (
    HeldLock,
    _RELEASE_SCRIPT_BODY,
    acquire,
    lock_context_manager,
    try_lock,
)


@pytest.fixture(scope="module")
def module_redis():
    """Module-scoped Redis client (avoids recomputing BLPOP timeouts)."""
    return redis_lib.Redis(host="127.0.0.1", port=6379, db=1, password="123456", decode_responses=True)


@pytest.fixture
def clean_module_redis(module_redis, raw_redis):
    """flushdb before each test; reuse the session-scoped raw_redis for flush."""
    raw_redis.flushdb()
    return module_redis


class TestReleaseScriptBody:
    def test_body_is_string(self):
        assert isinstance(_RELEASE_SCRIPT_BODY, str)
        assert "redis.call" in _RELEASE_SCRIPT_BODY
        assert "GET" in _RELEASE_SCRIPT_BODY
        assert "DEL" in _RELEASE_SCRIPT_BODY
        assert "RPUSH" in _RELEASE_SCRIPT_BODY


class TestHeldLockRelease:
    def test_release_deletes_lock_key(self, clean_module_redis):
        r = clean_module_redis
        r.set("gpdc:test:lock:x", "token-abc")
        held = HeldLock(r, "gpdc:test:lock:x", "gpdc:test:locknotify:x", "token-abc")
        held.release()
        assert not r.exists("gpdc:test:lock:x")

    def test_release_rpushes_notify(self, clean_module_redis):
        r = clean_module_redis
        r.set("gpdc:test:lock:x", "token-abc")
        held = HeldLock(r, "gpdc:test:lock:x", "gpdc:test:locknotify:x", "token-abc")
        held.release()
        assert r.llen("gpdc:test:locknotify:x") == 1

    def test_release_sets_notify_expiry(self, clean_module_redis):
        r = clean_module_redis
        r.set("gpdc:test:lock:x", "token-abc")
        held = HeldLock(r, "gpdc:test:lock:x", "gpdc:test:locknotify:x", "token-abc")
        held.release()
        ttl = r.ttl("gpdc:test:locknotify:x")
        assert 0 < ttl <= 5

    def test_release_token_mismatch_is_silent(self, clean_module_redis, caplog):
        import logging

        r = clean_module_redis
        r.set("gpdc:test:lock:x", "actual-token")
        held = HeldLock(r, "gpdc:test:lock:x", "gpdc:test:locknotify:x", "wrong-token")
        with caplog.at_level(logging.WARNING, logger="gpdatacached.locking"):
            # Must not raise.
            held.release()
        # Lock key still exists (was not ours to release).
        assert r.exists("gpdc:test:lock:x")
        # Notify queue untouched.
        assert not r.exists("gpdc:test:locknotify:x")
        # Warning was logged.
        assert any("lost before release" in rec.message for rec in caplog.records)

    def test_release_idempotent(self, clean_module_redis):
        r = clean_module_redis
        r.set("gpdc:test:lock:x", "token-abc")
        held = HeldLock(r, "gpdc:test:lock:x", "gpdc:test:locknotify:x", "token-abc")
        held.release()
        # Second release is a no-op (no error, no extra RPUSH).
        held.release()
        assert r.llen("gpdc:test:locknotify:x") == 1

    def test_context_manager_protocol(self, clean_module_redis):
        r = clean_module_redis
        r.set("gpdc:test:lock:x", "token-abc")
        held = HeldLock(r, "gpdc:test:lock:x", "gpdc:test:locknotify:x", "token-abc")
        with held as ctx:
            assert ctx is held
        assert not r.exists("gpdc:test:lock:x")

    def test_context_manager_releases_on_exception(self, clean_module_redis):
        r = clean_module_redis
        r.set("gpdc:test:lock:x", "token-abc")
        held = HeldLock(r, "gpdc:test:lock:x", "gpdc:test:locknotify:x", "token-abc")

        class Boom(Exception):
            pass

        with pytest.raises(Boom):
            with held:
                raise Boom()

        assert not r.exists("gpdc:test:lock:x")


class TestTryLock:
    def test_try_lock_succeeds_on_unlocked(self, clean_module_redis):
        r = clean_module_redis
        held = try_lock(r, "gpdc:test:lock:x", "gpdc:test:locknotify:x", ttl=10)
        assert held is not None
        assert r.get("gpdc:test:lock:x") == held.token
        held.release()

    def test_try_lock_returns_none_when_held(self, clean_module_redis):
        r = clean_module_redis
        first = try_lock(r, "gpdc:test:lock:x", "gpdc:test:locknotify:x", ttl=10)
        assert first is not None
        second = try_lock(r, "gpdc:test:lock:x", "gpdc:test:locknotify:x", ttl=10)
        assert second is None
        first.release()

    def test_try_lock_sets_ttl(self, clean_module_redis):
        r = clean_module_redis
        held = try_lock(r, "gpdc:test:lock:x", "gpdc:test:locknotify:x", ttl=15)
        assert held is not None
        ttl = r.ttl("gpdc:test:lock:x")
        assert 0 < ttl <= 15
        held.release()


class TestAcquire:
    def test_acquire_succeeds_on_unlocked(self, clean_module_redis):
        r = clean_module_redis
        held = acquire(r, "gpdc:test:lock:x", "gpdc:test:locknotify:x", ttl=10, timeout=1.0)
        assert isinstance(held, HeldLock)
        held.release()

    def test_acquire_timeout_raises(self, clean_module_redis):
        r = clean_module_redis
        blocker = try_lock(r, "gpdc:test:lock:x", "gpdc:test:locknotify:x", ttl=30)
        assert blocker is not None
        try:
            with pytest.raises(LockAcquisitionTimeout) as exc_info:
                acquire(r, "gpdc:test:lock:x", "gpdc:test:locknotify:x", ttl=10, timeout=0.5)
            assert exc_info.value.timeout == 0.5
            assert exc_info.value.lock_key == "gpdc:test:lock:x"
        finally:
            blocker.release()

    def test_acquire_wakes_on_release(self, clean_module_redis):
        """A waiter's BLPOP returns when the holder releases."""
        r = clean_module_redis

        # Holder grabs the lock.
        holder = try_lock(r, "gpdc:test:lock:x", "gpdc:test:locknotify:x", ttl=10)
        assert holder is not None

        acquired_at = {"time": None}

        def waiter():
            held = acquire(r, "gpdc:test:lock:x", "gpdc:test:locknotify:x", ttl=10, timeout=5.0)
            acquired_at["time"] = time.monotonic()
            held.release()

        t = threading.Thread(target=waiter)
        t.start()

        # Give the waiter a moment to enter BLPOP.
        time.sleep(0.2)
        release_time = time.monotonic()
        holder.release()

        t.join(timeout=3.0)
        assert not t.is_alive(), "waiter thread did not finish"
        assert acquired_at["time"] is not None
        # Waiter should have been woken within 200ms of release.
        assert acquired_at["time"] - release_time < 0.2


class TestLockContextManager:
    def test_basic_context_manager(self, clean_module_redis):
        r = clean_module_redis
        with lock_context_manager(r, "gpdc:test:lock:x", "gpdc:test:locknotify:x", ttl=10, timeout=1.0) as held:
            assert isinstance(held, HeldLock)
            assert r.exists("gpdc:test:lock:x")
        assert not r.exists("gpdc:test:lock:x")

    def test_context_manager_propagates_exception(self, clean_module_redis):
        r = clean_module_redis

        class Boom(Exception):
            pass

        with pytest.raises(Boom):
            with lock_context_manager(r, "gpdc:test:lock:x", "gpdc:test:locknotify:x", ttl=10, timeout=1.0):
                raise Boom()
        assert not r.exists("gpdc:test:lock:x")
