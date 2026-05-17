def test_union_returns_python_set(ns):
    ns["a"] = {1, 2}
    ns["b"] = {2, 3}
    a = ns.get_object("a")
    b = ns.get_object("b")
    result = a | b
    assert isinstance(result, set)
    assert not hasattr(result, "raw_key")
    assert result == {1, 2, 3}


def test_intersection_returns_python_set(ns):
    ns["a"] = {1, 2, 3}
    ns["b"] = {2, 3, 4}
    a = ns.get_object("a")
    b = ns.get_object("b")
    result = a & b
    assert isinstance(result, set)
    assert result == {2, 3}


def test_difference_returns_python_set(ns):
    ns["a"] = {1, 2, 3}
    ns["b"] = {2, 3, 4}
    a = ns.get_object("a")
    b = ns.get_object("b")
    result = a - b
    assert isinstance(result, set)
    assert result == {1}


def test_symmetric_difference_returns_python_set(ns):
    ns["a"] = {1, 2}
    ns["b"] = {2, 3}
    a = ns.get_object("a")
    b = ns.get_object("b")
    result = a ^ b
    assert isinstance(result, set)
    assert result == {1, 3}


def test_union_with_plain_set(ns):
    ns["a"] = {1, 2}
    a = ns.get_object("a")
    result = a | {2, 3}
    assert isinstance(result, set)
    assert result == {1, 2, 3}


def test_inplace_union_still_returns_self(ns):
    ns["a"] = {1, 2}
    a = ns.get_object("a")
    a |= {2, 3}
    assert a.value == {1, 2, 3}
