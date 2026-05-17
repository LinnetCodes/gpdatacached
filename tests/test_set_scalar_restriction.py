import pytest
import datetime


def test_set_accepts_int():
    from gpdatacached.codec import SetCodec

    encoded = SetCodec._encode_element(42)
    assert SetCodec._decode_element(encoded) == 42


def test_set_accepts_str():
    from gpdatacached.codec import SetCodec

    encoded = SetCodec._encode_element("hello")
    assert SetCodec._decode_element(encoded) == "hello"


def test_set_accepts_float():
    from gpdatacached.codec import SetCodec

    encoded = SetCodec._encode_element(3.14)
    assert SetCodec._decode_element(encoded) == 3.14


def test_set_accepts_bool():
    from gpdatacached.codec import SetCodec

    encoded = SetCodec._encode_element(True)
    assert SetCodec._decode_element(encoded) is True


def test_set_accepts_tuple():
    from gpdatacached.codec import SetCodec

    encoded = SetCodec._encode_element((1, 2))
    assert SetCodec._decode_element(encoded) == (1, 2)


def test_set_accepts_datetime():
    from gpdatacached.codec import SetCodec

    now = datetime.datetime(2024, 1, 1, 12, 0, 0)
    encoded = SetCodec._encode_element(now)
    decoded = SetCodec._decode_element(encoded)
    assert decoded == now


def test_set_accepts_none():
    from gpdatacached.codec import SetCodec

    encoded = SetCodec._encode_element(None)
    assert SetCodec._decode_element(encoded) is None


def test_set_rejects_list():
    from gpdatacached.codec import SetCodec

    with pytest.raises(TypeError, match="non-scalar"):
        SetCodec._encode_element([1, 2, 3])


def test_set_rejects_dict():
    from gpdatacached.codec import SetCodec

    with pytest.raises(TypeError, match="non-scalar"):
        SetCodec._encode_element({"a": 1})


def test_set_rejects_set():
    from gpdatacached.codec import SetCodec

    with pytest.raises(TypeError, match="non-scalar"):
        SetCodec._encode_element({1, 2})


def test_setobject_add_rejects_list(ns):
    ns["s"] = {1, 2, 3}
    s = ns.get_object("s")
    with pytest.raises(TypeError, match="non-scalar"):
        s.add([1, 2])


def test_setobject_create_rejects_nested(ns):
    with pytest.raises(TypeError):
        ns["s"] = {1, [2, 3]}


def test_setobject_copy_returns_python_set(ns):
    ns["s"] = {1, 2, 3}
    s = ns.get_object("s")
    result = s.copy()
    assert isinstance(result, set)
    assert result == {1, 2, 3}


def test_setobject_deepcopy_same_as_copy(ns):
    ns["s"] = {1, 2, 3}
    s = ns.get_object("s")
    assert s.deepcopy() == s.copy()
