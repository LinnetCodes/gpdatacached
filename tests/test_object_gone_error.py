import datetime
import pytest
from gpdatacached.exceptions import ObjectGoneError


def _delete_meta(obj, raw_redis):
    raw_redis.delete(obj._canonical_redis_key())


class TestObjectGoneErrorScalarSetters:
    def test_cache_mode_setter_raises(self, ns, raw_redis):
        ns["x"] = 42
        obj = ns.get_object("x")
        _delete_meta(obj, raw_redis)
        with pytest.raises(ObjectGoneError, match="no longer exists"):
            obj.cache_mode = "ttl"

    def test_ttl_setter_raises(self, ns, raw_redis):
        ns["x"] = 42
        obj = ns.get_object("x")
        _delete_meta(obj, raw_redis)
        with pytest.raises(ObjectGoneError, match="no longer exists"):
            obj.ttl = 100

    def test_expire_in_raises(self, ns, raw_redis):
        ns["x"] = 42
        obj = ns.get_object("x")
        _delete_meta(obj, raw_redis)
        with pytest.raises(ObjectGoneError, match="no longer exists"):
            obj.expire_in(60)

    def test_expire_at_raises(self, ns, raw_redis):
        ns["x"] = 42
        obj = ns.get_object("x")
        _delete_meta(obj, raw_redis)
        future = datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1)
        with pytest.raises(ObjectGoneError, match="no longer exists"):
            obj.expire_at(future)

    def test_persist_raises(self, ns, raw_redis):
        ns["x"] = 42
        obj = ns.get_object("x")
        _delete_meta(obj, raw_redis)
        with pytest.raises(ObjectGoneError, match="no longer exists"):
            obj.persist()

    def test_scalar_value_setter_raises(self, ns, raw_redis):
        ns["x"] = 42
        obj = ns.get_object("x")
        _delete_meta(obj, raw_redis)
        with pytest.raises(ObjectGoneError, match="no longer exists"):
            obj.value = 99


class TestObjectGoneErrorContainerTouch:
    def test_list_append_raises(self, ns, raw_redis):
        ns["lst"] = [1, 2]
        obj = ns.get_object("lst")
        _delete_meta(obj, raw_redis)
        with pytest.raises(ObjectGoneError, match="no longer exists"):
            obj.append(3)

    def test_set_add_raises(self, ns, raw_redis):
        ns["s"] = {1, 2}
        obj = ns.get_object("s")
        _delete_meta(obj, raw_redis)
        with pytest.raises(ObjectGoneError, match="no longer exists"):
            obj.add(3)

    def test_dict_setitem_raises(self, ns, raw_redis):
        ns["d"] = {"a": 1}
        obj = ns.get_object("d")
        _delete_meta(obj, raw_redis)
        with pytest.raises(ObjectGoneError, match="no longer exists"):
            obj["b"] = 2


class TestObjectGoneErrorAttributes:
    def test_exception_carries_uri(self, ns, raw_redis):
        ns["x"] = 42
        obj = ns.get_object("x")
        _delete_meta(obj, raw_redis)
        with pytest.raises(ObjectGoneError) as exc_info:
            obj.cache_mode = "ttl"
        assert exc_info.value.object_uri == obj.canonical_uri
        assert "gpdc://" in str(exc_info.value)
