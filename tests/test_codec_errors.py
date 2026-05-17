import pytest
from gpdatacached.codec import GPDCScalarCodec


def test_decode_invalid_int():
    with pytest.raises(ValueError, match="Invalid scalar value"):
        GPDCScalarCodec.decode_value("int:not_a_number")


def test_decode_invalid_float():
    with pytest.raises(ValueError, match="Invalid scalar value"):
        GPDCScalarCodec.decode_value("float:not_a_number")


def test_decode_unknown_type():
    with pytest.raises(ValueError, match="Unknown scalar type name"):
        GPDCScalarCodec.decode_value("unknown:value")


def test_decode_no_colon():
    with pytest.raises(ValueError, match="Invalid scalar encoding"):
        GPDCScalarCodec.decode_value("invalid_no_colon")


def test_decode_empty_string():
    with pytest.raises(ValueError, match="Invalid scalar encoding"):
        GPDCScalarCodec.decode_value("")


def test_encode_unsupported_type():
    with pytest.raises(TypeError, match="Unsupported scalar type"):
        GPDCScalarCodec.encode_value(complex(1, 2))


def test_decode_valid_values_roundtrip():
    values = [42, 3.14, "hello", True, False, None]
    for v in values:
        encoded = GPDCScalarCodec.encode_value(v)
        decoded = GPDCScalarCodec.decode_value(encoded)
        assert decoded == v, f"Roundtrip failed for {v!r}: got {decoded!r}"
