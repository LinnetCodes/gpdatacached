from __future__ import annotations

import datetime
import threading

import pytest
from pydantic import BaseModel

from gpdatacached.scalar import GPDCScalarObject
from gpdatacached.types import GPDCTypeRegistry
from gpdatacached.extensions.pydantic_support import BaseModelContainerObject, PydanticModelContainerCodec


class SimpleUser(BaseModel):
    name: str
    age: int


class UserWithDatetime(BaseModel):
    name: str
    created_at: datetime.datetime


class UserWithNone(BaseModel):
    name: str
    nickname: str | None


class UserWithBool(BaseModel):
    name: str
    active: bool


class UserWithFloat(BaseModel):
    name: str
    score: float


class UserWithDate(BaseModel):
    name: str
    birth_date: datetime.date


class UserWithTime(BaseModel):
    name: str
    alarm: datetime.time


class SimpleUserObject(GPDCScalarObject):
    TYPE = "scalar:SimpleUser"


class UserDatetimeObject(GPDCScalarObject):
    TYPE = "scalar:UserWithDatetime"


class UserNoneObject(GPDCScalarObject):
    TYPE = "scalar:UserWithNone"


class UserBoolObject(GPDCScalarObject):
    TYPE = "scalar:UserWithBool"


class UserFloatObject(GPDCScalarObject):
    TYPE = "scalar:UserWithFloat"


class UserDateObject(GPDCScalarObject):
    TYPE = "scalar:UserWithDate"


class UserTimeObject(GPDCScalarObject):
    TYPE = "scalar:UserWithTime"


class OrderModel(BaseModel):
    order_id: str
    total: float


class OrderWithList(BaseModel):
    order_id: str
    items: list[str]


class OrderWithDict(BaseModel):
    order_id: str
    metadata: dict[str, str]


class OrderModelObject(BaseModelContainerObject):
    TYPE = "collection:OrderModel"


class OrderWithListObject(BaseModelContainerObject):
    TYPE = "collection:OrderWithList"


class OrderWithDictObject(BaseModelContainerObject):
    TYPE = "collection:OrderWithDict"


@pytest.fixture(autouse=True)
def _register_pydantic_scalar_types():
    from gpdatacached.extensions.pydantic_support import PydanticModelScalarCodec

    original_gpdc = dict(GPDCTypeRegistry._by_gpdc_type)
    original_python = list(GPDCTypeRegistry._by_python_type)
    original_lock = GPDCTypeRegistry._lock
    try:
        GPDCTypeRegistry._lock = threading.Lock()
        GPDCTypeRegistry._by_gpdc_type = dict(original_gpdc)
        GPDCTypeRegistry._by_python_type = list(original_python)

        GPDCTypeRegistry.register(
            python_type=SimpleUser,
            gpdc_type="scalar:SimpleUser",
            object_class=SimpleUserObject,
            codec_class=PydanticModelScalarCodec,
            priority=0,
        )
        GPDCTypeRegistry.register(
            python_type=UserWithDatetime,
            gpdc_type="scalar:UserWithDatetime",
            object_class=UserDatetimeObject,
            codec_class=PydanticModelScalarCodec,
            priority=0,
        )
        GPDCTypeRegistry.register(
            python_type=UserWithNone,
            gpdc_type="scalar:UserWithNone",
            object_class=UserNoneObject,
            codec_class=PydanticModelScalarCodec,
            priority=0,
        )
        GPDCTypeRegistry.register(
            python_type=UserWithBool,
            gpdc_type="scalar:UserWithBool",
            object_class=UserBoolObject,
            codec_class=PydanticModelScalarCodec,
            priority=0,
        )
        GPDCTypeRegistry.register(
            python_type=UserWithFloat,
            gpdc_type="scalar:UserWithFloat",
            object_class=UserFloatObject,
            codec_class=PydanticModelScalarCodec,
            priority=0,
        )
        GPDCTypeRegistry.register(
            python_type=UserWithDate,
            gpdc_type="scalar:UserWithDate",
            object_class=UserDateObject,
            codec_class=PydanticModelScalarCodec,
            priority=0,
        )
        GPDCTypeRegistry.register(
            python_type=UserWithTime,
            gpdc_type="scalar:UserWithTime",
            object_class=UserTimeObject,
            codec_class=PydanticModelScalarCodec,
            priority=0,
        )
        yield
    finally:
        GPDCTypeRegistry._by_gpdc_type = original_gpdc
        GPDCTypeRegistry._by_python_type = original_python
        GPDCTypeRegistry._lock = original_lock


class TestPydanticScalarCodecEncode:
    def test_encode_simple_user(self):
        from gpdatacached.extensions.pydantic_support import PydanticModelScalarCodec

        user = SimpleUser(name="Alice", age=30)
        encoded = PydanticModelScalarCodec.encode_value(user)
        assert encoded.startswith("SimpleUser:")
        import json

        payload = json.loads(encoded[len("SimpleUser:") :])
        assert payload["name"] == "str:Alice"
        assert payload["age"] == "int:30"

    def test_encode_user_with_datetime(self):
        from gpdatacached.extensions.pydantic_support import PydanticModelScalarCodec

        dt = datetime.datetime(2024, 1, 1, 12, 0, 0)
        user = UserWithDatetime(name="Bob", created_at=dt)
        encoded = PydanticModelScalarCodec.encode_value(user)
        assert encoded.startswith("UserWithDatetime:")
        import json

        payload = json.loads(encoded[len("UserWithDatetime:") :])
        assert payload["created_at"] == "datetime:2024-01-01T12:00:00"

    def test_encode_user_with_none_field(self):
        from gpdatacached.extensions.pydantic_support import PydanticModelScalarCodec

        user = UserWithNone(name="Charlie", nickname=None)
        encoded = PydanticModelScalarCodec.encode_value(user)
        import json

        payload = json.loads(encoded[len("UserWithNone:") :])
        assert payload["nickname"] == "none:None"

    def test_encode_user_with_bool(self):
        from gpdatacached.extensions.pydantic_support import PydanticModelScalarCodec

        user = UserWithBool(name="Dave", active=True)
        encoded = PydanticModelScalarCodec.encode_value(user)
        import json

        payload = json.loads(encoded[len("UserWithBool:") :])
        assert payload["active"] == "bool:True"

    def test_encode_rejects_non_basemodel(self):
        from gpdatacached.extensions.pydantic_support import PydanticModelScalarCodec

        with pytest.raises(TypeError, match="BaseModel"):
            PydanticModelScalarCodec.encode_value("not a model")

    def test_encode_rejects_container_field_type(self):
        from gpdatacached.extensions.pydantic_support import PydanticModelScalarCodec

        class ModelWithList(BaseModel):
            items: list[str]

        class ModelWithListObject(GPDCScalarObject):
            TYPE = "scalar:ModelWithList"

        GPDCTypeRegistry.register(
            python_type=ModelWithList,
            gpdc_type="scalar:ModelWithList",
            object_class=ModelWithListObject,
            codec_class=PydanticModelScalarCodec,
            priority=0,
        )

        with pytest.raises(TypeError, match="container"):
            PydanticModelScalarCodec.encode_value(ModelWithList(items=["a"]))


class TestPydanticScalarCodecDecode:
    def test_decode_simple_user(self):
        from gpdatacached.extensions.pydantic_support import PydanticModelScalarCodec

        user = SimpleUser(name="Alice", age=30)
        encoded = PydanticModelScalarCodec.encode_value(user)
        decoded = PydanticModelScalarCodec.decode_value(encoded)
        assert isinstance(decoded, SimpleUser)
        assert decoded.name == "Alice"
        assert decoded.age == 30

    def test_decode_user_with_datetime(self):
        from gpdatacached.extensions.pydantic_support import PydanticModelScalarCodec

        dt = datetime.datetime(2024, 1, 1, 12, 0, 0)
        user = UserWithDatetime(name="Bob", created_at=dt)
        encoded = PydanticModelScalarCodec.encode_value(user)
        decoded = PydanticModelScalarCodec.decode_value(encoded)
        assert isinstance(decoded, UserWithDatetime)
        assert decoded.created_at == dt

    def test_decode_user_with_none(self):
        from gpdatacached.extensions.pydantic_support import PydanticModelScalarCodec

        user = UserWithNone(name="Charlie", nickname=None)
        encoded = PydanticModelScalarCodec.encode_value(user)
        decoded = PydanticModelScalarCodec.decode_value(encoded)
        assert decoded.nickname is None

    def test_decode_preserves_types(self):
        from gpdatacached.extensions.pydantic_support import PydanticModelScalarCodec

        user = UserWithBool(name="Dave", active=True)
        encoded = PydanticModelScalarCodec.encode_value(user)
        decoded = PydanticModelScalarCodec.decode_value(encoded)
        assert isinstance(decoded.active, bool)
        assert decoded.active is True


class TestPydanticScalarRoundtrip:
    def test_roundtrip_simple(self):
        from gpdatacached.extensions.pydantic_support import PydanticModelScalarCodec

        original = SimpleUser(name="Alice", age=30)
        encoded = PydanticModelScalarCodec.encode_value(original)
        decoded = PydanticModelScalarCodec.decode_value(encoded)
        assert decoded == original

    def test_roundtrip_with_float(self):
        from gpdatacached.extensions.pydantic_support import PydanticModelScalarCodec

        original = UserWithFloat(name="Eve", score=3.14)
        encoded = PydanticModelScalarCodec.encode_value(original)
        decoded = PydanticModelScalarCodec.decode_value(encoded)
        assert decoded == original

    def test_roundtrip_with_date(self):
        from gpdatacached.extensions.pydantic_support import PydanticModelScalarCodec

        original = UserWithDate(name="Frank", birth_date=datetime.date(1990, 5, 15))
        encoded = PydanticModelScalarCodec.encode_value(original)
        decoded = PydanticModelScalarCodec.decode_value(encoded)
        assert decoded == original

    def test_roundtrip_with_time(self):
        from gpdatacached.extensions.pydantic_support import PydanticModelScalarCodec

        original = UserWithTime(name="Grace", alarm=datetime.time(8, 30, 0))
        encoded = PydanticModelScalarCodec.encode_value(original)
        decoded = PydanticModelScalarCodec.decode_value(encoded)
        assert decoded == original


class TestPydanticScalarNamespaceIntegration:
    def test_set_and_get_model(self, ns):
        ns["user1"] = SimpleUser(name="Alice", age=30)
        result = ns["user1"]
        assert isinstance(result, SimpleUser)
        assert result.name == "Alice"
        assert result.age == 30

    def test_get_object_returns_correct_type(self, ns):
        ns["user1"] = SimpleUser(name="Alice", age=30)
        obj = ns.get_object("user1")
        assert obj.type == "scalar:SimpleUser"

    def test_update_model_via_object(self, ns):
        ns["user1"] = SimpleUser(name="Alice", age=30)
        obj = ns.get_object("user1")
        obj.value = SimpleUser(name="Bob", age=25)
        assert ns["user1"].name == "Bob"
        assert ns["user1"].age == 25

    def test_model_as_list_element(self, ns):
        ns["users"] = [SimpleUser(name="Alice", age=30), SimpleUser(name="Bob", age=25)]
        lst = ns["users"]
        assert isinstance(lst[0], SimpleUser)
        assert lst[0].name == "Alice"

    def test_model_as_dict_value(self, ns):
        ns["data"] = {"u1": SimpleUser(name="Alice", age=30)}
        d = ns["data"]
        assert isinstance(d["u1"], SimpleUser)
        assert d["u1"].name == "Alice"


@pytest.fixture
def register_container_types():
    original_gpdc = dict(GPDCTypeRegistry._by_gpdc_type)
    original_python = list(GPDCTypeRegistry._by_python_type)
    original_lock = GPDCTypeRegistry._lock
    try:
        GPDCTypeRegistry._lock = threading.Lock()
        GPDCTypeRegistry._by_gpdc_type = dict(original_gpdc)
        GPDCTypeRegistry._by_python_type = list(original_python)

        GPDCTypeRegistry.register(
            python_type=OrderModel,
            gpdc_type="collection:OrderModel",
            object_class=OrderModelObject,
            codec_class=PydanticModelContainerCodec,
            priority=0,
        )
        GPDCTypeRegistry.register(
            python_type=OrderWithList,
            gpdc_type="collection:OrderWithList",
            object_class=OrderWithListObject,
            codec_class=PydanticModelContainerCodec,
            priority=0,
        )
        GPDCTypeRegistry.register(
            python_type=OrderWithDict,
            gpdc_type="collection:OrderWithDict",
            object_class=OrderWithDictObject,
            codec_class=PydanticModelContainerCodec,
            priority=0,
        )
        yield
    finally:
        GPDCTypeRegistry._by_gpdc_type = original_gpdc
        GPDCTypeRegistry._by_python_type = original_python
        GPDCTypeRegistry._lock = original_lock


class TestPydanticContainerCodecCreateRaw:
    def test_create_raw_writes_hash(self, ns, register_container_types):
        raw_key = "gpdc:testdomain:testns:raw:test_order"
        try:
            order = OrderModel(order_id="ORD-001", total=99.5)
            PydanticModelContainerCodec.create_raw(ns, raw_key, order, "owner", "permanent", 0)
            assert ns._redis().hexists(raw_key, "order_id")
            assert ns._redis().hexists(raw_key, "total")
        finally:
            ns._redis().delete(raw_key)

    def test_create_raw_with_nested_list(self, ns, register_container_types):
        raw_key = "gpdc:testdomain:testns:raw:test_order_list"
        try:
            order = OrderWithList(order_id="ORD-002", items=["apple", "banana"])
            PydanticModelContainerCodec.create_raw(ns, raw_key, order, "owner", "permanent", 0)
            items_val = ns._redis().hget(raw_key, "items")
            assert items_val is not None
            assert items_val.startswith("ref:")
        finally:
            ns._redis().delete(raw_key)


class TestPydanticContainerCodecReadRaw:
    def test_read_raw_returns_dict(self, ns, register_container_types):
        raw_key = "gpdc:testdomain:testns:raw:test_order"
        try:
            order = OrderModel(order_id="ORD-001", total=99.5)
            PydanticModelContainerCodec.create_raw(ns, raw_key, order, "owner", "permanent", 0)
            result = PydanticModelContainerCodec.read_raw(ns, raw_key)
            assert isinstance(result, dict)
            assert result["order_id"] == "ORD-001"
            assert result["total"] == 99.5
        finally:
            ns._redis().delete(raw_key)

    def test_read_raw_empty_returns_empty_dict(self, ns, register_container_types):
        result = PydanticModelContainerCodec.read_raw(ns, "gpdc:nonexistent:key")
        assert result == {}


class TestPydanticContainerCodecDeleteRaw:
    def test_delete_raw(self, ns, register_container_types):
        raw_key = "gpdc:testdomain:testns:raw:test_order"
        order = OrderModel(order_id="ORD-001", total=99.5)
        PydanticModelContainerCodec.create_raw(ns, raw_key, order, "owner", "permanent", 0)
        assert ns._redis().exists(raw_key)
        PydanticModelContainerCodec.delete_raw(ns, raw_key)
        assert not ns._redis().exists(raw_key)


class TestPydanticContainerNamespaceIntegration:
    def test_set_and_get_model_container(self, ns, register_container_types):
        ns["order1"] = OrderModel(order_id="ORD-001", total=99.5)
        obj = ns["order1"]
        assert isinstance(obj, OrderModelObject)
        order = obj.value
        assert isinstance(order, OrderModel)
        assert order.order_id == "ORD-001"
        assert order.total == 99.5

    def test_field_level_get(self, ns, register_container_types):
        ns["order1"] = OrderModel(order_id="ORD-001", total=99.5)
        obj = ns["order1"]
        assert obj["order_id"] == "ORD-001"
        assert obj["total"] == 99.5

    def test_field_level_set(self, ns, register_container_types):
        ns["order1"] = OrderModel(order_id="ORD-001", total=99.5)
        obj = ns["order1"]
        obj["total"] = 199.5
        assert obj["total"] == 199.5
        assert obj.value.total == 199.5

    def test_field_set_unknown_key_raises(self, ns, register_container_types):
        ns["order1"] = OrderModel(order_id="ORD-001", total=99.5)
        obj = ns["order1"]
        with pytest.raises(KeyError, match="unknown_field"):
            obj["unknown_field"] = "value"

    def test_delete_field_raises(self, ns, register_container_types):
        ns["order1"] = OrderModel(order_id="ORD-001", total=99.5)
        obj = ns["order1"]
        with pytest.raises(TypeError, match="Cannot delete"):
            del obj["order_id"]

    def test_iter_yields_field_names(self, ns, register_container_types):
        ns["order1"] = OrderModel(order_id="ORD-001", total=99.5)
        obj = ns["order1"]
        assert set(obj) == {"order_id", "total"}

    def test_len_returns_field_count(self, ns, register_container_types):
        ns["order1"] = OrderModel(order_id="ORD-001", total=99.5)
        obj = ns["order1"]
        assert len(obj) == 2

    def test_contains(self, ns, register_container_types):
        ns["order1"] = OrderModel(order_id="ORD-001", total=99.5)
        obj = ns["order1"]
        assert "order_id" in obj
        assert "nonexistent" not in obj

    def test_nested_list_field(self, ns, register_container_types):
        ns["order1"] = OrderWithList(order_id="ORD-001", items=["apple", "banana"])
        obj = ns["order1"]
        items_obj = obj["items"]
        items_obj.append("cherry")
        assert obj.value.items == ["apple", "banana", "cherry"]

    def test_deepcopy(self, ns, register_container_types):
        ns["order1"] = OrderModel(order_id="ORD-001", total=99.5)
        obj = ns["order1"]
        copied = obj.deepcopy()
        assert isinstance(copied, OrderModel)
        assert copied == OrderModel(order_id="ORD-001", total=99.5)

    def test_delete_container_from_namespace(self, ns, register_container_types):
        ns["order1"] = OrderModel(order_id="ORD-001", total=99.5)
        assert "order1" in ns
        del ns["order1"]
        assert "order1" not in ns


class TestPydanticContainerGC:
    def test_gc_preserves_pydantic_container(self, ns, clean_redis, register_container_types):
        from gpdatacached.gc import run_gc_for_namespace
        from gpdatacached.keys import make_object_key, make_raw_key

        ns["order1"] = OrderModel(order_id="ORD-001", total=99.5)
        run_gc_for_namespace(clean_redis, "testdomain", "testns")
        assert clean_redis.exists(make_object_key("testdomain", "testns", "order1"))
        assert clean_redis.exists(make_raw_key("testdomain", "testns", "order1"))

    def test_gc_cleans_orphan_pydantic_container_raw(self, ns, clean_redis, register_container_types):
        from gpdatacached.gc import run_gc_for_namespace
        from gpdatacached.keys import make_object_key, make_raw_key, make_ref_key

        ns["order1"] = OrderModel(order_id="ORD-001", total=99.5)
        raw_key = make_raw_key("testdomain", "testns", "order1")
        clean_redis.delete(make_object_key("testdomain", "testns", "order1"))
        clean_redis.delete(make_ref_key("testdomain", "testns", "order1"))
        assert clean_redis.exists(raw_key)
        run_gc_for_namespace(clean_redis, "testdomain", "testns")
        assert not clean_redis.exists(raw_key)

    def test_gc_scans_nested_refs_in_pydantic_container(self, ns, clean_redis, register_container_types):
        from gpdatacached.gc import run_gc_for_namespace
        from gpdatacached.keys import make_ref_key

        ns["target"] = [100, 200]
        ns["order1"] = OrderWithList(order_id="ORD-001", items=["a", "b"])
        target_ref_key = make_ref_key("testdomain", "testns", "target")

        order_obj = ns["order1"]
        items_obj = order_obj["items"]
        items_obj.append(ns["target"])

        referrers = set(clean_redis.hkeys(target_ref_key))
        assert len(referrers) >= 1
        items_referrer = next(iter(referrers))

        items_raw_key = items_obj.raw_key
        clean_redis.delete(items_raw_key)
        clean_redis.rpush(items_raw_key, "str:fallback")

        run_gc_for_namespace(clean_redis, "testdomain", "testns")
        assert not clean_redis.hexists(target_ref_key, items_referrer)


class TestPydanticContainerTTL:
    def test_sliding_ttl_touch(self, ns, register_container_types):
        ns["order1"] = OrderModel(order_id="ORD-001", total=99.5)
        obj = ns.get_object("order1")
        obj.update_cache_mode_and_ttl("sliding", 3600)
        obj["total"] = 199.5
        remaining = obj.remaining_ttl()
        assert remaining > 0
