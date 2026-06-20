"""Tests for custom exceptions."""

from gpdatacached.exceptions import CanonicalObjectReferencedError
from gpdatacached.exceptions import GPDCError, LifecyclePermissionError
from gpdatacached.exceptions import (
    GPDCLockConfigInconsistentError,
    GPDCLockError,
    LockAcquisitionTimeout,
    LockOwnershipError,
)


class TestCanonicalObjectReferencedError:
    def test_instantiation(self):
        err = CanonicalObjectReferencedError(
            canonical_name="mylist",
            canonical_uri="gpdc://dom/ns/mylist",
            referrers=["alias1", "parent"],
        )
        assert err.canonical_name == "mylist"
        assert err.canonical_uri == "gpdc://dom/ns/mylist"
        assert err.referrers == ["alias1", "parent"]

    def test_message_format(self):
        err = CanonicalObjectReferencedError(
            canonical_name="mylist",
            canonical_uri="gpdc://dom/ns/mylist",
            referrers=["alias1", "parent"],
        )
        msg = str(err)
        assert "mylist" in msg
        assert "gpdc://dom/ns/mylist" in msg
        assert "alias1" in msg
        assert "parent" in msg
        assert "still referenced by" in msg

    def test_is_exception(self):
        err = CanonicalObjectReferencedError(
            canonical_name="x",
            canonical_uri="gpdc://d/n/x",
            referrers=["y"],
        )
        assert isinstance(err, Exception)

    def test_exported_from_package(self):
        from gpdatacached import CanonicalObjectReferencedError as Imported

        assert Imported is CanonicalObjectReferencedError


class TestLifecyclePermissionError:
    def test_is_gpdc_error(self):
        assert issubclass(LifecyclePermissionError, GPDCError)

    def test_message(self):
        err = LifecyclePermissionError(canonical_name="~anon:123", owner_name="myobj")
        assert "~anon:123" in str(err)
        assert "myobj" in str(err)
        assert err.canonical_name == "~anon:123"
        assert err.owner_name == "myobj"


class TestGPDCLockError:
    def test_is_gpdc_error(self):
        assert issubclass(GPDCLockError, GPDCError)


class TestLockAcquisitionTimeout:
    def test_instantiation(self):
        err = LockAcquisitionTimeout(lock_key="gpdc:d:n:lock:x", timeout=5.0)
        assert err.lock_key == "gpdc:d:n:lock:x"
        assert err.timeout == 5.0

    def test_message_format(self):
        err = LockAcquisitionTimeout(lock_key="gpdc:d:n:lock:x", timeout=5.0)
        msg = str(err)
        assert "gpdc:d:n:lock:x" in msg
        assert "5" in msg

    def test_is_gpdc_lock_error(self):
        assert issubclass(LockAcquisitionTimeout, GPDCLockError)


class TestLockOwnershipError:
    def test_is_gpdc_lock_error(self):
        assert issubclass(LockOwnershipError, GPDCLockError)


class TestGPDCLockConfigInconsistentError:
    def test_instantiation(self):
        err = GPDCLockConfigInconsistentError(
            domain="mydomain",
            conflicts={"default_lock_ttl_seconds": ("30", "60")},
            legal_values={"default_lock_ttl_seconds": "30"},
        )
        assert err.domain == "mydomain"
        assert err.conflicts == {"default_lock_ttl_seconds": ("30", "60")}
        assert err.legal_values == {"default_lock_ttl_seconds": "30"}

    def test_message_format_with_legal_values(self):
        err = GPDCLockConfigInconsistentError(
            domain="mydomain",
            conflicts={"default_lock_ttl_seconds": ("30", "60")},
            legal_values={
                "default_lock_ttl_seconds": "30",
                "default_lock_acquire_timeout_seconds": "30",
            },
        )
        msg = str(err)
        assert "mydomain" in msg
        assert "default_lock_ttl_seconds" in msg
        assert "redis='30'" in msg
        assert "your config='60'" in msg
        assert "Legal values" in msg
        assert "default_lock_acquire_timeout_seconds = 30" in msg
        assert "reset_domain_config" in msg

    def test_message_format_no_legal_values(self):
        err = GPDCLockConfigInconsistentError(
            domain="mydomain",
            conflicts={"default_lock_ttl_seconds": ("30", "60")},
            legal_values={},
        )
        msg = str(err)
        assert "(none)" in msg

    def test_is_gpdc_error(self):
        # Note: GPDCLockConfigInconsistentError extends GPDCError directly,
        # NOT GPDCLockError (it's a configuration error, not a lock-acquisition error).
        assert issubclass(GPDCLockConfigInconsistentError, GPDCError)
        assert not issubclass(GPDCLockConfigInconsistentError, GPDCLockError)
