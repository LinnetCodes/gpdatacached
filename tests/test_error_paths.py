import pytest


class TestCodecErrorPaths:
    def test_decode_empty_string(self):
        from gpdatacached.codec import GPDCScalarCodec

        with pytest.raises(ValueError, match="Invalid scalar encoding"):
            GPDCScalarCodec.decode_value("")

    def test_decode_no_colon(self):
        from gpdatacached.codec import GPDCScalarCodec

        with pytest.raises(ValueError, match="Invalid scalar encoding"):
            GPDCScalarCodec.decode_value("invalid")

    def test_decode_bad_int(self):
        from gpdatacached.codec import GPDCScalarCodec

        with pytest.raises(ValueError, match="Invalid scalar value"):
            GPDCScalarCodec.decode_value("int:abc")

    def test_decode_bad_float(self):
        from gpdatacached.codec import GPDCScalarCodec

        with pytest.raises(ValueError, match="Invalid scalar value"):
            GPDCScalarCodec.decode_value("float:xyz")

    def test_decode_bad_datetime(self):
        from gpdatacached.codec import GPDCScalarCodec

        with pytest.raises(ValueError, match="Invalid scalar value"):
            GPDCScalarCodec.decode_value("datetime:not-a-date")

    def test_decode_bad_date(self):
        from gpdatacached.codec import GPDCScalarCodec

        with pytest.raises(ValueError, match="Invalid scalar value"):
            GPDCScalarCodec.decode_value("date:bad")

    def test_decode_bad_time(self):
        from gpdatacached.codec import GPDCScalarCodec

        with pytest.raises(ValueError, match="Invalid scalar value"):
            GPDCScalarCodec.decode_value("time:bad")

    def test_encode_unsupported_type(self):
        from gpdatacached.codec import GPDCScalarCodec

        with pytest.raises(TypeError, match="Unsupported scalar type"):
            GPDCScalarCodec.encode_value(complex(1, 2))

    def test_encode_empty_tuple(self):
        from gpdatacached.codec import GPDCScalarCodec

        with pytest.raises(ValueError, match="Cannot encode empty tuple"):
            GPDCScalarCodec.encode_tuple(())

    def test_encode_tuple_with_list_element(self):
        from gpdatacached.codec import GPDCScalarCodec

        with pytest.raises(TypeError, match="non-scalar"):
            GPDCScalarCodec.encode_tuple(([1, 2],))


class TestNamespaceErrorPaths:
    def test_getitem_missing_key(self, ns):
        with pytest.raises(KeyError):
            ns["nonexistent"]

    def test_delitem_missing_key(self, ns):
        with pytest.raises(KeyError):
            del ns["nonexistent"]

    def test_setitem_invalid_name(self, ns):
        with pytest.raises(ValueError):
            ns["__reserved__"] = 42

    def test_setitem_invalid_chars(self, ns):
        with pytest.raises(ValueError):
            ns["has space"] = 42

    def test_setitem_cross_namespace_ref(self):
        from gpdatacached.config import GPDCDomainConfig
        from gpdatacached.domain import GPDCDomain
        import redis as redis_lib

        r = redis_lib.Redis(host="127.0.0.1", port=6379, db=1, password="123456", decode_responses=True)
        r.flushdb()
        cfg = GPDCDomainConfig(redis_host="127.0.0.1", redis_port=6379, redis_db=1, redis_password="123456")
        domain = GPDCDomain("testdomain", cfg)
        ns1 = domain.get_namespace("ns1")
        ns2 = domain.get_namespace("ns2")
        ns1["lst"] = [1, 2, 3]
        lst = ns1.get_object("lst")
        with pytest.raises(ValueError, match="Cross-namespace"):
            ns2["ref"] = lst
        domain.close()

    def test_setitem_unsupported_type(self, ns):
        with pytest.raises(TypeError, match="Unsupported Python type"):
            ns["x"] = complex(1, 2)


class TestContainerErrorPaths:
    def test_list_index_out_of_range(self, ns):
        ns["lst"] = [1, 2, 3]
        lst = ns.get_object("lst")
        with pytest.raises(IndexError):
            lst[10]

    def test_list_pop_empty(self, ns):
        ns["lst"] = []
        lst = ns.get_object("lst")
        with pytest.raises(IndexError, match="empty list"):
            lst.pop()

    def test_list_remove_missing(self, ns):
        ns["lst"] = [1, 2, 3]
        lst = ns.get_object("lst")
        with pytest.raises(ValueError, match="not in list"):
            lst.remove(99)

    def test_dict_key_error(self, ns):
        ns["d"] = {"a": 1}
        d = ns.get_object("d")
        with pytest.raises(KeyError):
            d["nonexistent"]

    def test_dict_del_missing_key(self, ns):
        ns["d"] = {"a": 1}
        d = ns.get_object("d")
        with pytest.raises(KeyError):
            del d["nonexistent"]

    def test_set_pop_empty(self, ns):
        ns["s"] = set()
        s = ns.get_object("s")
        with pytest.raises(KeyError, match="empty set"):
            s.pop()

    def test_set_remove_missing(self, ns):
        ns["s"] = {1, 2}
        s = ns.get_object("s")
        with pytest.raises(KeyError):
            s.remove(99)


class TestRegistryErrorPaths:
    def test_lookup_unsupported_python_type(self):
        from gpdatacached.types import GPDCTypeRegistry

        with pytest.raises(TypeError, match="Unsupported Python type"):
            GPDCTypeRegistry.lookup_by_python_type(complex(1, 2))

    def test_lookup_unknown_gpdc_type(self):
        from gpdatacached.types import GPDCTypeRegistry

        with pytest.raises(ValueError, match="Unknown GPDC type"):
            GPDCTypeRegistry.lookup_by_gpdc_type("unknown:type")

    def test_invalid_cache_mode(self, ns):
        ns["x"] = 42
        obj = ns.get_object("x")
        with pytest.raises(ValueError, match="Invalid cache_mode"):
            obj.cache_mode = "invalid"


class TestKeysErrorPaths:
    def test_uri_invalid_prefix(self):
        from gpdatacached.keys import uri_to_redis_key

        with pytest.raises(ValueError, match="must start with"):
            uri_to_redis_key("http://domain/ns/name")

    def test_uri_invalid_format(self):
        from gpdatacached.keys import uri_to_redis_key

        with pytest.raises(ValueError, match="must be gpdc://"):
            uri_to_redis_key("gpdc://domain/name")

    def test_validate_reserved_name(self):
        from gpdatacached.keys import validate_object_name

        with pytest.raises(ValueError, match="reserved"):
            validate_object_name("__meta__")

    def test_validate_invalid_chars(self):
        from gpdatacached.keys import validate_object_name

        with pytest.raises(ValueError, match="only letters"):
            validate_object_name("has spaces")
