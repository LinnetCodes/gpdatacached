"""Tests for encode memo: cycle detection (H2) and aliasing preservation (H3)."""

from __future__ import annotations

from gpdatacached import CyclicReferenceError
from gpdatacached.exceptions import GPDCError


class TestCyclicReferenceError:
    def test_is_gpdc_error_subclass(self):
        assert issubclass(CyclicReferenceError, GPDCError)

    def test_message_contains_type_name(self):
        err = CyclicReferenceError("dict")
        assert "dict" in str(err)
        assert err.type_name == "dict"

    def test_exported_from_package(self):
        from gpdatacached import CyclicReferenceError as ImportedException

        assert ImportedException is CyclicReferenceError
