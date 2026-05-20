import threading
from decimal import Decimal

import pytest

from gpdatacached.codec import GPDCCodec
from gpdatacached.scalar import GPDCScalarObject
from gpdatacached.types import GPDCTypeRegistry


class DecimalCodec(GPDCCodec):
    @classmethod
    def encode_value(cls, value: object) -> str:
        if not isinstance(value, Decimal):
            raise TypeError(f"DecimalCodec cannot encode {type(value).__name__!r}")
        return f"decimal:{value}"

    @classmethod
    def decode_value(cls, encoded: str) -> object:
        if not encoded or ":" not in encoded:
            raise ValueError(f"Invalid encoding: {encoded!r}")
        colon_idx = encoded.index(":")
        type_name = encoded[:colon_idx]
        raw = encoded[colon_idx + 1 :]
        if type_name != "decimal":
            raise ValueError(f"DecimalCodec cannot decode type {type_name!r}")
        return Decimal(raw)


class DecimalObject(GPDCScalarObject):
    TYPE = "scalar:decimal"


@pytest.fixture(autouse=True)
def register_decimal():
    original_gpdc = dict(GPDCTypeRegistry._by_gpdc_type)
    original_python = list(GPDCTypeRegistry._by_python_type)
    original_lock = GPDCTypeRegistry._lock
    try:
        GPDCTypeRegistry._lock = threading.Lock()
        GPDCTypeRegistry._by_gpdc_type = dict(original_gpdc)
        GPDCTypeRegistry._by_python_type = list(original_python)

        GPDCTypeRegistry.register(
            python_type=Decimal,
            gpdc_type="scalar:decimal",
            object_class=DecimalObject,
            codec_class=DecimalCodec,
            priority=0,
        )
        yield
    finally:
        GPDCTypeRegistry._by_gpdc_type = original_gpdc
        GPDCTypeRegistry._by_python_type = original_python
        GPDCTypeRegistry._lock = original_lock


class TestCustomScalarStandalone:
    def test_encode_decode(self):
        encoded = DecimalCodec.encode_value(Decimal("3.14"))
        assert encoded == "decimal:3.14"
        decoded = DecimalCodec.decode_value(encoded)
        assert decoded == Decimal("3.14")


class TestCustomScalarInList:
    def test_list_element(self, ns):
        ns["mylist"] = [Decimal("1.5"), Decimal("2.5")]
        lst = ns.get_object("mylist")
        assert lst[0] == Decimal("1.5")
        assert lst[1] == Decimal("2.5")


class TestCustomScalarInDict:
    def test_dict_value(self, ns):
        ns["mydict"] = {"price": Decimal("9.99")}
        d = ns.get_object("mydict")
        assert d["price"] == Decimal("9.99")

    def test_dict_key(self, ns):
        ns["mydict"] = {Decimal("1"): "one", Decimal("2"): "two"}
        d = ns.get_object("mydict")
        assert d[Decimal("1")] == "one"
        assert d[Decimal("2")] == "two"


class TestCustomScalarInSet:
    def test_set_element(self, ns):
        ns["myset"] = {Decimal("1"), Decimal("2"), Decimal("3")}
        s = ns.get_object("myset")
        assert Decimal("1") in s
        assert len(s) == 3


class TestCustomScalarInTuple:
    def test_tuple_with_custom_scalar(self):
        from gpdatacached.codec import TupleCodec

        original = (Decimal("1.5"), "hello", 42)
        encoded = TupleCodec.encode_value(original)
        decoded = TupleCodec.decode_value(encoded)
        assert decoded == original
        assert decoded[0] == Decimal("1.5")
