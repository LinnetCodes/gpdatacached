"""Tests for SetCodec (Phase 4)."""

from __future__ import annotations

import pytest
import redis as redis_lib

from gpdatacached.codec import SetCodec

REDIS_HOST = "127.0.0.1"
REDIS_PORT = 6379
REDIS_DB = 1
REDIS_PASSWORD = "123456"

RAW_KEY = "test:setcodec:raw:myset"


@pytest.fixture(scope="module")
def r():
    client = redis_lib.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD,
        decode_responses=True,
    )
    yield client
    client.close()


@pytest.fixture(autouse=True)
def cleanup(r):
    r.delete(RAW_KEY)
    yield
    r.delete(RAW_KEY)


class TestSetCodecCreateRaw:
    def test_create_raw_integers(self, r):
        SetCodec.create_raw(r, RAW_KEY, {1, 2, 3})
        members = r.smembers(RAW_KEY)
        assert members == {"int:1", "int:2", "int:3"}

    def test_create_raw_strings(self, r):
        SetCodec.create_raw(r, RAW_KEY, {"a", "b"})
        members = r.smembers(RAW_KEY)
        assert members == {"str:a", "str:b"}

    def test_create_raw_mixed_scalars(self, r):
        SetCodec.create_raw(r, RAW_KEY, {1, "hello", 3.14})
        members = r.smembers(RAW_KEY)
        assert "int:1" in members
        assert "str:hello" in members
        assert "float:3.14" in members

    def test_create_raw_tuple_elements(self, r):
        SetCodec.create_raw(r, RAW_KEY, {1, (2, 3)})
        members = r.smembers(RAW_KEY)
        assert "int:1" in members
        assert any(m.startswith("tuple:") for m in members)

    def test_create_raw_empty_set(self, r):
        SetCodec.create_raw(r, RAW_KEY, set())
        assert r.exists(RAW_KEY) == 0


class TestSetCodecReadRaw:
    def test_read_raw_integers(self, r):
        SetCodec.create_raw(r, RAW_KEY, {1, 2, 3})
        result = SetCodec.read_raw(r, RAW_KEY)
        assert result == {1, 2, 3}

    def test_read_raw_mixed_scalars(self, r):
        SetCodec.create_raw(r, RAW_KEY, {1, "hello", 3.14})
        result = SetCodec.read_raw(r, RAW_KEY)
        assert result == {1, "hello", 3.14}

    def test_read_raw_tuple_elements(self, r):
        original = {1, (2, 3)}
        SetCodec.create_raw(r, RAW_KEY, original)
        result = SetCodec.read_raw(r, RAW_KEY)
        assert result == original

    def test_read_raw_missing_key_returns_empty(self, r):
        result = SetCodec.read_raw(r, "nonexistent:key")
        assert result == set()


class TestSetCodecDeleteRaw:
    def test_delete_raw(self, r):
        SetCodec.create_raw(r, RAW_KEY, {1, 2})
        assert r.exists(RAW_KEY) == 1
        SetCodec.delete_raw(r, RAW_KEY)
        assert r.exists(RAW_KEY) == 0


class TestSetCodecRoundTrip:
    def test_roundtrip_scalars(self, r):
        original = {1, 2, 3}
        SetCodec.create_raw(r, RAW_KEY, original)
        assert SetCodec.read_raw(r, RAW_KEY) == original

    def test_roundtrip_mixed_with_tuples(self, r):
        original = {1, "x", (2, 3)}
        SetCodec.create_raw(r, RAW_KEY, original)
        assert SetCodec.read_raw(r, RAW_KEY) == original
