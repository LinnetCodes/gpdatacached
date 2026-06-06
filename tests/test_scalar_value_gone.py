import pytest
from gpdatacached.exceptions import GPDCError, ObjectGoneError


class TestScalarValueDeleted:
    def test_int_value_raises_object_gone_error(self, ns, raw_redis):
        ns["x"] = 42
        obj = ns.get_object("x")
        raw_redis.delete(obj._canonical_redis_key())
        with pytest.raises(ObjectGoneError):
            _ = obj.value

    def test_str_value_raises_object_gone_error(self, ns, raw_redis):
        ns["s"] = "hello"
        obj = ns.get_object("s")
        raw_redis.delete(obj._canonical_redis_key())
        with pytest.raises(ObjectGoneError):
            _ = obj.value


class TestScalarValueCorrupted:
    def test_corrupted_int_raises_gpdc_error(self, ns, raw_redis):
        ns["x"] = 42
        obj = ns.get_object("x")
        raw_redis.hset(obj._canonical_redis_key(), "value", "")
        with pytest.raises(GPDCError, match="Corrupted"):
            _ = obj.value

    def test_corrupted_str_raises_gpdc_error(self, ns, raw_redis):
        ns["s"] = "hello"
        obj = ns.get_object("s")
        raw_redis.hset(obj._canonical_redis_key(), "value", "")
        with pytest.raises(GPDCError, match="Corrupted"):
            _ = obj.value


class TestScalarValueNoneObject:
    def test_none_object_returns_none(self, ns):
        ns["n"] = None
        obj = ns.get_object("n")
        assert obj.value is None

    def test_none_object_value_roundtrip(self, ns):
        ns["n"] = None
        obj = ns.get_object("n")
        obj.value = None
        assert obj.value is None
