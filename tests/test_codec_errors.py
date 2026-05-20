import pytest
from gpdatacached.codec import IntCodec, FloatCodec, BoolCodec


def test_decode_invalid_int():
    with pytest.raises(ValueError, match="Invalid scalar value"):
        IntCodec.decode_value("int:not_a_number")


def test_decode_invalid_float():
    with pytest.raises(ValueError, match="Invalid scalar value"):
        FloatCodec.decode_value("float:not_a_number")


def test_decode_unknown_type():
    with pytest.raises(ValueError, match="cannot decode type"):
        IntCodec.decode_value("unknown:value")


def test_decode_no_colon():
    with pytest.raises(ValueError, match="Invalid scalar encoding"):
        IntCodec.decode_value("invalid_no_colon")


def test_decode_empty_string():
    with pytest.raises(ValueError, match="Invalid scalar encoding"):
        IntCodec.decode_value("")


def test_encode_unsupported_type():
    with pytest.raises(TypeError):
        IntCodec.encode_value(complex(1, 2))


def test_decode_valid_values_roundtrip():
    from gpdatacached.codec import FloatCodec, StrCodec, NoneCodec

    values_and_codecs = [
        (42, IntCodec),
        (3.14, FloatCodec),
        ("hello", StrCodec),
        (True, BoolCodec),
        (False, BoolCodec),
        (None, NoneCodec),
    ]
    for v, codec in values_and_codecs:
        encoded = codec.encode_value(v)
        decoded = codec.decode_value(encoded)
        assert decoded == v, f"Roundtrip failed for {v!r}: got {decoded!r}"
