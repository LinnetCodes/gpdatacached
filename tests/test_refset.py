"""Unit tests for refset module."""

import pytest

from gpdatacached.refset import (
    add_referrer,
    release_ref,
    remove_referrer,
    get_referrers,
    has_referrers,
)


@pytest.fixture
def r(clean_redis):
    return clean_redis


@pytest.fixture
def ref_key():
    return "gpdc:testdomain:testns:ref:testobj"


class TestAddReferrer:
    def test_add_first_referrer(self, r, ref_key):
        add_referrer(r, ref_key, "parent")
        assert r.hget(ref_key, "parent") == "1"

    def test_add_same_referrer_twice_increments_count(self, r, ref_key):
        add_referrer(r, ref_key, "parent")
        add_referrer(r, ref_key, "parent")
        assert r.hget(ref_key, "parent") == "2"

    def test_add_multiple_referrers(self, r, ref_key):
        add_referrer(r, ref_key, "parent")
        add_referrer(r, ref_key, "alias")
        assert r.hget(ref_key, "parent") == "1"
        assert r.hget(ref_key, "alias") == "1"


class TestReleaseRef:
    def test_release_decrements_count(self, r, ref_key):
        add_referrer(r, ref_key, "parent")
        add_referrer(r, ref_key, "parent")
        fully_released = release_ref(r, ref_key, "parent")
        assert not fully_released
        assert r.hget(ref_key, "parent") == "1"

    def test_release_removes_entry_at_zero(self, r, ref_key):
        add_referrer(r, ref_key, "parent")
        fully_released = release_ref(r, ref_key, "parent")
        assert fully_released
        assert r.hexists(ref_key, "parent") is False

    def test_release_on_nonexistent_returns_true(self, r, ref_key):
        fully_released = release_ref(r, ref_key, "ghost")
        assert fully_released

    def test_double_release_removes_entry(self, r, ref_key):
        add_referrer(r, ref_key, "parent")
        release_ref(r, ref_key, "parent")
        fully_released = release_ref(r, ref_key, "parent")
        assert fully_released
        assert not r.hexists(ref_key, "parent")


class TestRemoveReferrer:
    def test_remove_entirely(self, r, ref_key):
        add_referrer(r, ref_key, "parent")
        add_referrer(r, ref_key, "parent")
        remove_referrer(r, ref_key, "parent")
        assert r.hexists(ref_key, "parent") is False

    def test_remove_nonexistent_is_noop(self, r, ref_key):
        remove_referrer(r, ref_key, "ghost")
        assert r.hlen(ref_key) == 0


class TestGetReferrers:
    def test_empty_ref_set(self, r, ref_key):
        assert get_referrers(r, ref_key) == set()

    def test_returns_all_referrer_names(self, r, ref_key):
        add_referrer(r, ref_key, "parent")
        add_referrer(r, ref_key, "alias")
        result = get_referrers(r, ref_key)
        assert result == {"parent", "alias"}


class TestHasReferrers:
    def test_empty_returns_false(self, r, ref_key):
        assert has_referrers(r, ref_key) is False

    def test_with_referrers_returns_true(self, r, ref_key):
        add_referrer(r, ref_key, "parent")
        assert has_referrers(r, ref_key) is True

    def test_after_full_release_returns_false(self, r, ref_key):
        add_referrer(r, ref_key, "parent")
        release_ref(r, ref_key, "parent")
        assert has_referrers(r, ref_key) is False
