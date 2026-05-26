"""Tests for custom exceptions."""

from gpdatacached.exceptions import CanonicalObjectReferencedError


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
