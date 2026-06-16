import threading
from decimal import Decimal

import pytest

from gpdatacached.codec import GPDCContainerCodec, GPDCScalarCodec
from gpdatacached.container import GPDCContainerObject
from gpdatacached.scalar import GPDCScalarObject
from gpdatacached.types import GPDCTypeRegistry


class DecimalCodec(GPDCScalarCodec):
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


class LenlessContainer:
    """A container-like type that deliberately lacks __len__."""

    def __init__(self, items):
        self._items = list(items)

    def __iter__(self):
        return iter(self._items)

    def __eq__(self, other):
        return isinstance(other, LenlessContainer) and self._items == other._items


class LenlessCodec(GPDCContainerCodec):
    @classmethod
    def create_raw(cls, namespace, raw_key, value, owner_name, cache_mode, ttl):
        from gpdatacached.lifecycle import policy_from_defaults
        from gpdatacached.types import encode_element

        if not value._items:
            return
        policy = policy_from_defaults(cache_mode, ttl)
        encoded = [encode_element(v, namespace, owner_name, policy) for v in value._items]
        namespace._redis().rpush(raw_key, *encoded)

    @classmethod
    def read_raw(cls, namespace, raw_key):
        from gpdatacached.types import decode_element

        items = namespace._redis().lrange(raw_key, 0, -1)
        return LenlessContainer([decode_element(item, namespace) for item in items])

    @classmethod
    def delete_raw(cls, namespace, raw_key):
        namespace._redis().delete(raw_key)


class LenlessObject(GPDCContainerObject):
    TYPE = "collection:lenless"

    @property
    def type(self):
        return self.TYPE

    @property
    def value(self):
        return LenlessCodec.read_raw(self._namespace, self.raw_key)

    @value.setter
    def value(self, new_value):
        self._reject_value_replacement()

    def __len__(self):
        return self._redis().llen(self.raw_key)

    def __iter__(self):
        from gpdatacached.types import decode_element

        items = self._redis().lrange(self.raw_key, 0, -1)
        for item in items:
            yield decode_element(item, self._namespace)

    def deepcopy(self):
        return self.value


class TestTypeRegistryResolve:
    def test_resolve_int(self):
        entry = GPDCTypeRegistry.lookup_by_python_type(42)
        assert entry.gpdc_type == "scalar:int"

    def test_resolve_float(self):
        entry = GPDCTypeRegistry.lookup_by_python_type(3.14)
        assert entry.gpdc_type == "scalar:float"

    def test_resolve_str(self):
        entry = GPDCTypeRegistry.lookup_by_python_type("hello")
        assert entry.gpdc_type == "scalar:str"

    def test_resolve_bool_before_int(self):
        entry = GPDCTypeRegistry.lookup_by_python_type(True)
        assert entry.gpdc_type == "scalar:bool"

    def test_resolve_none(self):
        entry = GPDCTypeRegistry.lookup_by_python_type(None)
        assert entry.gpdc_type == "scalar:none"

    def test_resolve_list(self):
        entry = GPDCTypeRegistry.lookup_by_python_type([1, 2, 3])
        assert entry.gpdc_type == "collection:list"

    def test_resolve_dict(self):
        entry = GPDCTypeRegistry.lookup_by_python_type({"a": 1})
        assert entry.gpdc_type == "collection:dict"

    def test_resolve_set(self):
        entry = GPDCTypeRegistry.lookup_by_python_type({1, 2, 3})
        assert entry.gpdc_type == "collection:set"

    def test_resolve_unsupported_type_raises(self):
        with pytest.raises(TypeError, match="Unsupported Python type"):
            GPDCTypeRegistry.lookup_by_python_type(object())


class TestTypeRegistryLookup:
    def test_lookup_scalar_int(self):
        from gpdatacached import IntObject

        entry = GPDCTypeRegistry.lookup_by_gpdc_type("scalar:int")
        assert entry.object_class is IntObject

    def test_lookup_collection_list(self):
        from gpdatacached import ListObject

        entry = GPDCTypeRegistry.lookup_by_gpdc_type("collection:list")
        assert entry.object_class is ListObject

    def test_lookup_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown GPDC type"):
            GPDCTypeRegistry.lookup_by_gpdc_type("unknown:foo")


class TestTypeEntryIsContainer:
    def test_scalar_is_not_container(self):
        entry = GPDCTypeRegistry.lookup_by_gpdc_type("scalar:int")
        assert entry.is_container is False

    def test_list_is_container(self):
        entry = GPDCTypeRegistry.lookup_by_gpdc_type("collection:list")
        assert entry.is_container is True


class TestTypeRegistryExtension:
    def test_register_custom_type(self):
        original_gpdc = dict(GPDCTypeRegistry._by_gpdc_type)
        original_python = list(GPDCTypeRegistry._by_python_type)
        original_lock = GPDCTypeRegistry._lock
        try:

            class MyFakeObject:
                pass

            GPDCTypeRegistry._lock = threading.Lock()
            GPDCTypeRegistry._by_gpdc_type = dict(original_gpdc)
            GPDCTypeRegistry._by_python_type = list(original_python)

            class MyFakeCodec:
                @staticmethod
                def encode_value(value):
                    return f"bytes:{value!r}"

                @staticmethod
                def decode_value(encoded):
                    return eval(encoded[len("bytes:") :])

            GPDCTypeRegistry.register(
                python_type=bytes,
                gpdc_type="custom:bytes",
                object_class=MyFakeObject,
                codec_class=MyFakeCodec,
                priority=0,
            )
            entry = GPDCTypeRegistry.lookup_by_python_type(b"hello")
            assert entry.gpdc_type == "custom:bytes"
            assert entry.object_class is MyFakeObject
        finally:
            GPDCTypeRegistry._by_gpdc_type = original_gpdc
            GPDCTypeRegistry._by_python_type = original_python
            GPDCTypeRegistry._lock = original_lock


class TestBoolToIntObject:
    def test_bool_true_rejected_by_int_object(self, ns):
        ns["x"] = 42
        obj = ns.get_object("x")
        with pytest.raises(TypeError, match="IntCodec cannot encode 'bool'"):
            obj.value = True

    def test_bool_false_rejected_by_int_object(self, ns):
        ns["x"] = 42
        obj = ns.get_object("x")
        with pytest.raises(TypeError, match="IntCodec cannot encode 'bool'"):
            obj.value = False


class TestNoneObject:
    def test_get_object_returns_none_object(self, ns):
        ns["n"] = None
        obj = ns.get_object("n")
        assert obj.type == "scalar:none"
        assert obj.value is None

    def test_none_object_value_setter_to_none(self, ns):
        ns["n"] = None
        obj = ns.get_object("n")
        obj.value = None
        assert obj.value is None


class TestTypeRegistryThreading:
    def test_lookup_by_gpdc_type_returns_correct_entry(self):
        entry = GPDCTypeRegistry.lookup_by_gpdc_type("scalar:int")
        assert entry.gpdc_type == "scalar:int"
        assert entry.object_class is not None

    def test_lookup_by_gpdc_type_raises_on_unknown(self):
        with pytest.raises(ValueError, match="Unknown GPDC type"):
            GPDCTypeRegistry.lookup_by_gpdc_type("nonexistent:type")

    def test_lookup_by_python_type_returns_correct_entry(self):
        entry = GPDCTypeRegistry.lookup_by_python_type(42)
        assert entry.gpdc_type == "scalar:int"

    def test_lookup_by_python_type_bool_has_priority(self):
        entry = GPDCTypeRegistry.lookup_by_python_type(True)
        assert entry.gpdc_type == "scalar:bool"

    def test_lookup_by_python_type_raises_on_unsupported(self):

        class CustomClass:
            pass

        with pytest.raises(TypeError, match="Unsupported Python type"):
            GPDCTypeRegistry.lookup_by_python_type(CustomClass())

    def test_concurrent_registrations_do_not_corrupt(self):
        errors = []

        def register_and_lookup(idx):
            try:
                for _ in range(50):
                    entry = GPDCTypeRegistry.lookup_by_python_type(idx)
                    assert entry is not None
                    entry = GPDCTypeRegistry.lookup_by_gpdc_type("scalar:int")
                    assert entry is not None
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=register_and_lookup, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent access errors: {errors}"


class TestRegistryExtensibility:
    @pytest.fixture(autouse=True)
    def register_decimal(self):
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

    def test_encode_decode(self):
        encoded = DecimalCodec.encode_value(Decimal("3.14"))
        assert encoded == "decimal:3.14"
        decoded = DecimalCodec.decode_value(encoded)
        assert decoded == Decimal("3.14")

    def test_list_element(self, ns):
        ns["mylist"] = [Decimal("1.5"), Decimal("2.5")]
        lst = ns.get_object("mylist")
        assert lst[0] == Decimal("1.5")
        assert lst[1] == Decimal("2.5")

    def test_dict_value(self, ns):
        ns["mydict"] = {"price": Decimal("9.99")}
        d = ns.get_object("mydict")
        assert d["price"] == Decimal("9.99")

    def test_dict_key(self, ns):
        ns["mydict"] = {Decimal("1"): "one", Decimal("2"): "two"}
        d = ns.get_object("mydict")
        assert d[Decimal("1")] == "one"
        assert d[Decimal("2")] == "two"

    def test_set_element(self, ns):
        ns["myset"] = {Decimal("1"), Decimal("2"), Decimal("3")}
        s = ns.get_object("myset")
        assert Decimal("1") in s
        assert len(s) == 3

    def test_tuple_with_custom_scalar(self):
        from gpdatacached import TupleCodec

        original = (Decimal("1.5"), "hello", 42)
        encoded = TupleCodec.encode_value(original)
        decoded = TupleCodec.decode_value(encoded)
        assert decoded == original
        assert decoded[0] == Decimal("1.5")


class TestRegistryErrorPaths:
    def test_lookup_unsupported_python_type(self):
        with pytest.raises(TypeError, match="Unsupported Python type"):
            GPDCTypeRegistry.lookup_by_python_type(complex(1, 2))

    def test_lookup_unknown_gpdc_type(self):
        with pytest.raises(ValueError, match="Unknown GPDC type"):
            GPDCTypeRegistry.lookup_by_gpdc_type("unknown:type")

    def test_invalid_cache_mode(self, ns):
        ns["x"] = 42
        obj = ns.get_object("x")
        with pytest.raises(ValueError, match="Invalid cache_mode"):
            obj.update_cache_mode_and_ttl("invalid", 0)


class TestLenlessContainerGuard:
    """H5: _create_anonymous_object must guard len() for containers without __len__."""

    @pytest.fixture(autouse=True)
    def register_lenless(self):
        original_gpdc = dict(GPDCTypeRegistry._by_gpdc_type)
        original_python = list(GPDCTypeRegistry._by_python_type)
        original_lock = GPDCTypeRegistry._lock
        try:
            GPDCTypeRegistry._lock = threading.Lock()
            GPDCTypeRegistry._by_gpdc_type = dict(original_gpdc)
            GPDCTypeRegistry._by_python_type = list(original_python)

            GPDCTypeRegistry.register(
                python_type=LenlessContainer,
                gpdc_type="collection:lenless",
                object_class=LenlessObject,
                codec_class=LenlessCodec,
                priority=0,
            )
            yield
        finally:
            GPDCTypeRegistry._by_gpdc_type = original_gpdc
            GPDCTypeRegistry._by_python_type = original_python
            GPDCTypeRegistry._lock = original_lock

    def test_nested_lenless_container_does_not_crash(self, ns):
        ns["x"] = {"inner": LenlessContainer([1, 2, 3])}
        d = ns["x"]
        assert isinstance(d["inner"], LenlessObject)

    def test_anon_meta_item_count_is_none(self, ns, raw_redis):
        from gpdatacached.keys import make_object_key, make_raw_key, parse_ref_encoded

        ns["x"] = {"inner": LenlessContainer([1, 2, 3])}
        raw_data = raw_redis.hgetall(make_raw_key("testdomain", "testns", "x"))
        ref_str = raw_data["str:inner"]
        anon_name = parse_ref_encoded(ref_str)
        anon_meta = raw_redis.hgetall(make_object_key("testdomain", "testns", anon_name))
        assert anon_meta.get("item_count", "") == ""

    def test_round_trip(self, ns):
        ns["x"] = {"inner": LenlessContainer([1, 2, 3])}
        d = ns["x"]
        inner = d["inner"]
        assert list(inner) == [1, 2, 3]
