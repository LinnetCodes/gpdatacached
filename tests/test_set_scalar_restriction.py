import pytest
import datetime


def test_set_accepts_int():
    from gpdatacached.types import encode_key, decode_key

    encoded = encode_key(42)
    assert decode_key(encoded) == 42


def test_set_accepts_str():
    from gpdatacached.types import encode_key, decode_key

    encoded = encode_key("hello")
    assert decode_key(encoded) == "hello"


def test_set_accepts_float():
    from gpdatacached.types import encode_key, decode_key

    encoded = encode_key(3.14)
    assert decode_key(encoded) == 3.14


def test_set_accepts_bool():
    from gpdatacached.types import encode_key, decode_key

    encoded = encode_key(True)
    assert decode_key(encoded) is True


def test_set_accepts_tuple():
    from gpdatacached.types import encode_key, decode_key

    encoded = encode_key((1, 2))
    assert decode_key(encoded) == (1, 2)


def test_set_accepts_datetime():
    from gpdatacached.types import encode_key, decode_key

    now = datetime.datetime(2024, 1, 1, 12, 0, 0)
    encoded = encode_key(now)
    decoded = decode_key(encoded)
    assert decoded == now


def test_set_accepts_none():
    from gpdatacached.types import encode_key, decode_key

    encoded = encode_key(None)
    assert decode_key(encoded) is None


def test_set_rejects_list():
    from gpdatacached.types import encode_key

    with pytest.raises(TypeError, match="container type"):
        encode_key([1, 2, 3])


def test_set_rejects_dict():
    from gpdatacached.types import encode_key

    with pytest.raises(TypeError, match="container type"):
        encode_key({"a": 1})


def test_set_rejects_set():
    from gpdatacached.types import encode_key

    with pytest.raises(TypeError, match="container type"):
        encode_key({1, 2})


def test_setobject_add_rejects_list(ns):
    ns["s"] = {1, 2, 3}
    s = ns.get_object("s")
    with pytest.raises(TypeError, match="container type"):
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
