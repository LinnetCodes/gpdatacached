"""Unit tests for the locking primitives."""

from __future__ import annotations

import pytest
import redis as redis_lib

from gpdatacached.locking import HeldLock, _RELEASE_SCRIPT_BODY


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
