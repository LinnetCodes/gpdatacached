from __future__ import annotations

import datetime
import threading

import pytest
from pydantic import BaseModel

from gpdatacached.scalar import GPDCScalarObject
from gpdatacached.types import GPDCTypeRegistry


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
