import pytest


def test_setitem_scalar_new(ns):
    ns["x"] = 42
    assert ns["x"] == 42


def test_setitem_scalar_overwrite(ns):
    ns["x"] = 42
    ns["x"] = "hello"
    assert ns["x"] == "hello"


def test_setitem_list_new(ns):
    ns["lst"] = [1, 2, 3]
    lst = ns["lst"]
    assert lst.value == [1, 2, 3]


def test_setitem_dict_new(ns):
    ns["d"] = {"a": 1}
    d = ns["d"]
    assert d.value == {"a": 1}


def test_setitem_set_new(ns):
    ns["s"] = {1, 2, 3}
    s = ns["s"]
    assert s.value == {1, 2, 3}


def test_setitem_none(ns):
    ns["n"] = None
    assert ns["n"] is None


def test_setitem_bool(ns):
    ns["b"] = True
    assert ns["b"] is True


def test_setitem_scalar_object(ns):
    ns["x"] = 42
    obj = ns.get_object("x")
    ns["y"] = obj
    assert ns["y"] == 42


@pytest.mark.skip(reason="Pending refactor")
def test_setitem_list_owner_overwrite_to_scalar_rejected(ns):
    ns["x"] = [1, 2, 3]

    with pytest.raises(TypeError, match="Cannot reassign a namespace container owner"):
        ns["x"] = 42


@pytest.mark.skip(reason="Pending refactor")
def test_setitem_owner_overwrite_to_native_container_rejected(ns):
    ns["x"] = [1, 2, 3]

    with pytest.raises(TypeError, match="Cannot reassign a namespace container owner"):
        ns["x"] = {"a": 1}


def test_setitem_referrer_can_rebind_to_scalar(ns):
    ns["x"] = [1, 2, 3]
    ns["y"] = ns["x"]

    ns["y"] = 42

    assert ns["y"] == 42
    assert list(ns["x"]) == [1, 2, 3]


def test_setitem_referrer_can_rebind_to_native_container(ns):
    ns["x"] = [1, 2, 3]
    ns["y"] = ns["x"]

    ns["y"] = {"a": 1}

    assert ns["y"]["a"] == 1
    assert list(ns["x"]) == [1, 2, 3]


def test_setitem_referrer_can_rebind_to_existing_container_object(ns):
    ns["x"] = [1, 2, 3]
    ns["y"] = ns["x"]
    ns["z"] = {"a": 1}

    ns["y"] = ns["z"]

    assert ns["y"]["a"] == 1
    assert list(ns["x"]) == [1, 2, 3]


def test_contains_with_tilde_name(ns):
    assert "~anon:abc" not in ns


def test_contains_with_empty_string(ns):
    assert "" not in ns


def test_contains_with_non_string(ns):
    assert 42 not in ns


def test_contains_existing_key(ns):
    ns["x"] = 42
    assert "x" in ns


def test_contains_nonexistent_key(ns):
    assert "nonexistent" not in ns
