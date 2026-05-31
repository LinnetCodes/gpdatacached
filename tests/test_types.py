import pytest
from gpdatacached.types import GPDCTypeRegistry


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
        from gpdatacached.scalar import IntObject

        entry = GPDCTypeRegistry.lookup_by_gpdc_type("scalar:int")
        assert entry.object_class is IntObject

    def test_lookup_collection_list(self):
        from gpdatacached.container import ListObject

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
        import threading

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


class TestNoneObjectViaGetObj:
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

    def test_none_object_repr(self, ns):
        ns["n"] = None
        obj = ns.get_object("n")
        assert "NoneObject" in repr(obj)

    def test_none_object_uri(self, ns):
        ns["n"] = None
        obj = ns.get_object("n")
        assert obj.uri == "gpdc://testdomain/testns/n"


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
