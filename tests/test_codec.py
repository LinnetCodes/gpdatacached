import datetime
import pytest
from gpdatacached.codec import GPDCScalarCodec


class TestGPDCScalarCodecEncode:
    def test_encode_int(self):
        assert GPDCScalarCodec.encode_value(42) == "int:42"

    def test_encode_float(self):
        assert GPDCScalarCodec.encode_value(1.5) == "float:1.5"

    def test_encode_str(self):
        assert GPDCScalarCodec.encode_value("test") == "str:test"

    def test_encode_str_with_colon(self):
        assert GPDCScalarCodec.encode_value("hello:world") == "str:hello:world"

    def test_encode_bool_true(self):
        assert GPDCScalarCodec.encode_value(True) == "bool:True"

    def test_encode_bool_false(self):
        assert GPDCScalarCodec.encode_value(False) == "bool:False"

    def test_encode_bool_before_int(self):
        # bool is subclass of int; must be encoded as bool, not int
        result = GPDCScalarCodec.encode_value(True)
        assert result.startswith("bool:"), f"Expected bool:..., got {result}"

    def test_encode_datetime(self):
        dt = datetime.datetime(2024, 1, 1, 12, 30, 0)
        assert GPDCScalarCodec.encode_value(dt) == "datetime:2024-01-01T12:30:00"

    def test_encode_date(self):
        d = datetime.date(2024, 1, 1)
        assert GPDCScalarCodec.encode_value(d) == "date:2024-01-01"

    def test_encode_time(self):
        t = datetime.time(12, 30, 0)
        assert GPDCScalarCodec.encode_value(t) == "time:12:30:00"

    def test_encode_none(self):
        assert GPDCScalarCodec.encode_value(None) == "none:None"

    def test_encode_unsupported_raises(self):
        with pytest.raises(TypeError):
            GPDCScalarCodec.encode_value([1, 2, 3])

    def test_encode_alias(self):
        # encode() is alias for encode_value()
        assert GPDCScalarCodec.encode(42) == "int:42"


class TestGPDCScalarCodecDecode:
    def test_decode_int(self):
        assert GPDCScalarCodec.decode_value("int:42") == 42
        assert isinstance(GPDCScalarCodec.decode_value("int:42"), int)

    def test_decode_float(self):
        assert GPDCScalarCodec.decode_value("float:1.5") == 1.5

    def test_decode_str(self):
        assert GPDCScalarCodec.decode_value("str:test") == "test"

    def test_decode_str_with_colon(self):
        # Decode takes everything after the first colon
        assert GPDCScalarCodec.decode_value("str:hello:world") == "hello:world"

    def test_decode_str_empty(self):
        assert GPDCScalarCodec.decode_value("str:") == ""

    def test_decode_bool_true(self):
        result = GPDCScalarCodec.decode_value("bool:True")
        assert result is True
        assert isinstance(result, bool)

    def test_decode_bool_false(self):
        result = GPDCScalarCodec.decode_value("bool:False")
        assert result is False
        assert isinstance(result, bool)

    def test_decode_datetime(self):
        result = GPDCScalarCodec.decode_value("datetime:2024-01-01T12:30:00")
        assert result == datetime.datetime(2024, 1, 1, 12, 30, 0)

    def test_decode_date(self):
        result = GPDCScalarCodec.decode_value("date:2024-01-01")
        assert result == datetime.date(2024, 1, 1)

    def test_decode_time(self):
        result = GPDCScalarCodec.decode_value("time:12:30:00")
        assert result == datetime.time(12, 30, 0)

    def test_decode_none(self):
        assert GPDCScalarCodec.decode_value("none:None") is None

    def test_decode_unknown_type_raises(self):
        with pytest.raises(ValueError):
            GPDCScalarCodec.decode_value("unknown:value")

    def test_decode_alias(self):
        assert GPDCScalarCodec.decode("int:42") == 42


class TestGPDCScalarCodecRoundtrip:
    @pytest.mark.parametrize("value", [
        42, -1, 0,
        1.5, -0.5, 3.14,
        "hello", "hello:world", "", "with spaces",
        True, False,
        datetime.datetime(2024, 6, 15, 9, 0, 0),
        datetime.date(2024, 6, 15),
        datetime.time(9, 0, 0),
        None,
    ])
    def test_roundtrip(self, value):
        encoded = GPDCScalarCodec.encode_value(value)
        decoded = GPDCScalarCodec.decode_value(encoded)
        assert decoded == value
        assert type(decoded) is type(value)


class TestGPDCScalarCodecTypeOf:
    def test_gpdc_type_of_int(self):
        assert GPDCScalarCodec.gpdc_type_of(42) == "scalar:int"

    def test_gpdc_type_of_bool(self):
        assert GPDCScalarCodec.gpdc_type_of(True) == "scalar:bool"

    def test_gpdc_type_of_none(self):
        assert GPDCScalarCodec.gpdc_type_of(None) == "scalar:none"

    def test_gpdc_type_of_str(self):
        assert GPDCScalarCodec.gpdc_type_of("hello") == "scalar:str"

    def test_gpdc_type_of_datetime(self):
        assert GPDCScalarCodec.gpdc_type_of(datetime.datetime(2024, 1, 1)) == "scalar:datetime"

    def test_gpdc_type_of_date(self):
        # date must not match datetime (datetime is checked first)
        assert GPDCScalarCodec.gpdc_type_of(datetime.date(2024, 1, 1)) == "scalar:date"
