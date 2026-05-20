import datetime
import pytest
from gpdatacached.codec import (
    NoneCodec,
    BoolCodec,
    IntCodec,
    FloatCodec,
    StrCodec,
    DateTimeCodec,
    DateCodec,
    TimeCodec,
    TupleCodec,
    ListCodec,
    DictCodec,
)


@pytest.fixture
def raw_key(ns):
    key = "test:listcodec:tmp"
    ns._redis().delete(key)
    yield key
    ns._redis().delete(key)


class TestScalarCodecEncode:
    def test_encode_int(self):
        assert IntCodec.encode_value(42) == "int:42"

    def test_encode_float(self):
        assert FloatCodec.encode_value(1.5) == "float:1.5"

    def test_encode_str(self):
        assert StrCodec.encode_value("test") == "str:test"

    def test_encode_str_with_colon(self):
        assert StrCodec.encode_value("hello:world") == "str:hello:world"

    def test_encode_bool_true(self):
        assert BoolCodec.encode_value(True) == "bool:True"

    def test_encode_bool_false(self):
        assert BoolCodec.encode_value(False) == "bool:False"

    def test_encode_bool_before_int(self):
        result = BoolCodec.encode_value(True)
        assert result.startswith("bool:"), f"Expected bool:..., got {result}"

    def test_encode_datetime(self):
        dt = datetime.datetime(2024, 1, 1, 12, 30, 0)
        assert DateTimeCodec.encode_value(dt) == "datetime:2024-01-01T12:30:00"

    def test_encode_date(self):
        d = datetime.date(2024, 1, 1)
        assert DateCodec.encode_value(d) == "date:2024-01-01"

    def test_encode_time(self):
        t = datetime.time(12, 30, 0)
        assert TimeCodec.encode_value(t) == "time:12:30:00"

    def test_encode_none(self):
        assert NoneCodec.encode_value(None) == "none:None"

    def test_encode_unsupported_raises(self):
        with pytest.raises(TypeError):
            IntCodec.encode_value([1, 2, 3])


class TestScalarCodecDecode:
    def test_decode_int(self):
        assert IntCodec.decode_value("int:42") == 42
        assert isinstance(IntCodec.decode_value("int:42"), int)

    def test_decode_float(self):
        assert FloatCodec.decode_value("float:1.5") == 1.5

    def test_decode_str(self):
        assert StrCodec.decode_value("str:test") == "test"

    def test_decode_str_with_colon(self):
        assert StrCodec.decode_value("str:hello:world") == "hello:world"

    def test_decode_str_empty(self):
        assert StrCodec.decode_value("str:") == ""

    def test_decode_bool_true(self):
        result = BoolCodec.decode_value("bool:True")
        assert result is True
        assert isinstance(result, bool)

    def test_decode_bool_false(self):
        result = BoolCodec.decode_value("bool:False")
        assert result is False
        assert isinstance(result, bool)

    def test_decode_datetime(self):
        result = DateTimeCodec.decode_value("datetime:2024-01-01T12:30:00")
        assert result == datetime.datetime(2024, 1, 1, 12, 30, 0)

    def test_decode_date(self):
        result = DateCodec.decode_value("date:2024-01-01")
        assert result == datetime.date(2024, 1, 1)

    def test_decode_time(self):
        result = TimeCodec.decode_value("time:12:30:00")
        assert result == datetime.time(12, 30, 0)

    def test_decode_none(self):
        assert NoneCodec.decode_value("none:None") is None

    def test_decode_unknown_type_raises(self):
        with pytest.raises(ValueError):
            IntCodec.decode_value("unknown:value")


class TestScalarCodecRoundtrip:
    @pytest.mark.parametrize(
        "value,codec",
        [
            (42, IntCodec),
            (-1, IntCodec),
            (0, IntCodec),
            (1.5, FloatCodec),
            (-0.5, FloatCodec),
            (3.14, FloatCodec),
            ("hello", StrCodec),
            ("hello:world", StrCodec),
            ("", StrCodec),
            ("with spaces", StrCodec),
            (True, BoolCodec),
            (False, BoolCodec),
            (datetime.datetime(2024, 6, 15, 9, 0, 0), DateTimeCodec),
            (datetime.date(2024, 6, 15), DateCodec),
            (datetime.time(9, 0, 0), TimeCodec),
            (None, NoneCodec),
        ],
    )
    def test_roundtrip(self, value, codec):
        encoded = codec.encode_value(value)
        decoded = codec.decode_value(encoded)
        assert decoded == value
        assert type(decoded) is type(value)


class TestTupleEncoding:
    def test_encode_tuple_str_pair(self):
        result = TupleCodec.encode_value(("a", "b"))
        assert result == 'tuple:["str:a","str:b"]'

    def test_encode_tuple_mixed_types(self):
        result = TupleCodec.encode_value((1, "x", 2.5))
        assert result == 'tuple:["int:1","str:x","float:2.5"]'

    def test_encode_tuple_bool_none(self):
        result = TupleCodec.encode_value((True, None))
        assert result == 'tuple:["bool:True","none:None"]'

    def test_encode_tuple_single_element(self):
        result = TupleCodec.encode_value(("a",))
        assert result == 'tuple:["str:a"]'

    def test_decode_tuple_str_pair(self):
        result = TupleCodec.decode_value('tuple:["str:a","str:b"]')
        assert result == ("a", "b")

    def test_decode_tuple_mixed_types(self):
        result = TupleCodec.decode_value('tuple:["int:1","str:x","float:2.5"]')
        assert result == (1, "x", 2.5)

    def test_decode_tuple_bool_none(self):
        result = TupleCodec.decode_value('tuple:["bool:True","none:None"]')
        assert result == (True, None)

    def test_roundtrip_tuple(self):
        original = ("hello", 42, 3.14, True, None)
        encoded = TupleCodec.encode_value(original)
        decoded = TupleCodec.decode_value(encoded)
        assert decoded == original

    def test_encode_tuple_rejects_empty(self):
        with pytest.raises(ValueError, match="empty tuple"):
            TupleCodec.encode_value(())

    def test_encode_tuple_rejects_non_scalar(self):
        with pytest.raises(TypeError):
            TupleCodec.encode_value(([1, 2],))


class TestListCodec:
    def test_create_raw_scalars(self, ns, raw_key):
        ListCodec.create_raw(ns, raw_key, [1, 2, 3], "~test", "permanent", 0)
        items = ns._redis().lrange(raw_key, 0, -1)
        assert items == ["int:1", "int:2", "int:3"]

    def test_create_raw_mixed_types(self, ns, raw_key):
        ListCodec.create_raw(ns, raw_key, [1, "hello", True, None], "~test", "permanent", 0)
        items = ns._redis().lrange(raw_key, 0, -1)
        assert items == ["int:1", "str:hello", "bool:True", "none:None"]

    def test_create_raw_empty_list(self, ns, raw_key):
        ListCodec.create_raw(ns, raw_key, [], "~test", "permanent", 0)
        assert not ns._redis().exists(raw_key)

    def test_read_raw_returns_correct_list(self, ns, raw_key):
        ListCodec.create_raw(ns, raw_key, [1, 2, 3], "~test", "permanent", 0)
        result = ListCodec.read_raw(ns, raw_key)
        assert result == [1, 2, 3]

    def test_read_raw_missing_key_returns_empty(self, ns, raw_key):
        result = ListCodec.read_raw(ns, raw_key)
        assert result == []

    def test_read_raw_mixed_types(self, ns, raw_key):
        ListCodec.create_raw(ns, raw_key, [1, "hello", True, None], "~test", "permanent", 0)
        result = ListCodec.read_raw(ns, raw_key)
        assert result == [1, "hello", True, None]

    def test_delete_raw(self, ns, raw_key):
        ListCodec.create_raw(ns, raw_key, [1, 2, 3], "~test", "permanent", 0)
        ListCodec.delete_raw(ns, raw_key)
        assert not ns._redis().exists(raw_key)

    def test_delete_raw_missing_key_no_error(self, ns, raw_key):
        ListCodec.delete_raw(ns, raw_key)


class TestDictCodec:
    def test_create_raw_writes_hash(self, ns):
        raw_key = "gpdc:test:d:ns:raw:testdict"
        try:
            DictCodec.create_raw(ns, raw_key, {"name": "Alice", "age": 30}, "~test", "permanent", 0)
            assert ns._redis().hexists(raw_key, "str:name")
            assert ns._redis().hget(raw_key, "str:name") == "str:Alice"
            assert ns._redis().hget(raw_key, "str:age") == "int:30"
        finally:
            ns._redis().delete(raw_key)

    def test_read_raw_returns_dict(self, ns):
        raw_key = "gpdc:test:d:ns:raw:testdict"
        try:
            ns._redis().hset(raw_key, mapping={"str:name": "str:Alice", "str:age": "int:30"})
            result = DictCodec.read_raw(ns, raw_key)
            assert result == {"name": "Alice", "age": 30}
        finally:
            ns._redis().delete(raw_key)

    def test_read_raw_empty_key(self, ns):
        result = DictCodec.read_raw(ns, "gpdc:nonexistent:key")
        assert result == {}

    def test_delete_raw(self, ns):
        raw_key = "gpdc:test:d:ns:raw:testdict"
        ns._redis().hset(raw_key, "str:x", "str:y")
        DictCodec.delete_raw(ns, raw_key)
        assert not ns._redis().exists(raw_key)

    def test_create_raw_with_tuple_key(self, ns):
        raw_key = "gpdc:test:d:ns:raw:testdict"
        try:
            data = {"text": "test", 2: 100, ("a", "b"): "example"}
            DictCodec.create_raw(ns, raw_key, data, "~test", "permanent", 0)
            assert ns._redis().hget(raw_key, "str:text") == "str:test"
            assert ns._redis().hget(raw_key, "int:2") == "int:100"
            assert ns._redis().hget(raw_key, 'tuple:["str:a","str:b"]') == "str:example"
        finally:
            ns._redis().delete(raw_key)

    def test_read_raw_with_tuple_key(self, ns):
        raw_key = "gpdc:test:d:ns:raw:testdict"
        try:
            ns._redis().hset(
                raw_key,
                mapping={
                    "str:text": "str:test",
                    "int:2": "int:100",
                    'tuple:["str:a","str:b"]': "str:example",
                },
            )
            result = DictCodec.read_raw(ns, raw_key)
            assert result["text"] == "test"
            assert result[2] == 100
            assert result[("a", "b")] == "example"
        finally:
            ns._redis().delete(raw_key)

    def test_create_raw_empty_dict(self, ns):
        raw_key = "gpdc:test:d:ns:raw:testdict"
        DictCodec.create_raw(ns, raw_key, {}, "~test", "permanent", 0)
        assert ns._redis().exists(raw_key) == 0

    def test_create_raw_nests_list_value(self, ns):
        raw_key = "gpdc:test:d:ns:raw:testdict"
        try:
            DictCodec.create_raw(ns, raw_key, {"a": [1, 2]}, "~test", "permanent", 0)
            val = ns._redis().hget(raw_key, "str:a")
            assert val is not None
            assert val.startswith("ref:")
        finally:
            ns._redis().delete(raw_key)

    def test_create_raw_nests_dict_value(self, ns):
        raw_key = "gpdc:test:d:ns:raw:testdict"
        try:
            DictCodec.create_raw(ns, raw_key, {"a": {"x": 1}}, "~test", "permanent", 0)
            val = ns._redis().hget(raw_key, "str:a")
            assert val is not None
            assert val.startswith("ref:")
        finally:
            ns._redis().delete(raw_key)
