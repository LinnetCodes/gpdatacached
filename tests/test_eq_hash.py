import pytest


class TestEqIdentity:
    def test_same_key_equal(self, ns):
        ns["x"] = [1, 2, 3]
        a = ns.get_object("x")
        b = ns.get_object("x")
        assert a == b

    def test_different_key_not_equal(self, ns):
        ns["a"] = [1, 2]
        ns["b"] = [1, 2]
        assert ns.get_object("a") != ns.get_object("b")

    def test_scalar_same_key_equal(self, ns):
        ns["n"] = 42
        assert ns.get_object("n") == ns.get_object("n")

    def test_scalar_different_key_not_equal(self, ns):
        ns["x"] = 42
        ns["y"] = 42
        assert ns.get_object("x") != ns.get_object("y")

    def test_container_vs_python_returns_not_implemented(self, ns):
        ns["lst"] = [1, 2, 3]
        obj = ns.get_object("lst")
        assert obj != [1, 2, 3]

    def test_dict_object_vs_python_dict(self, ns):
        ns["d"] = {"a": 1}
        obj = ns.get_object("d")
        assert obj != {"a": 1}

    def test_set_object_vs_python_set(self, ns):
        ns["s"] = {1, 2}
        obj = ns.get_object("s")
        assert obj != {1, 2}

    def test_scalar_vs_python_value(self, ns):
        ns["x"] = 42
        obj = ns.get_object("x")
        assert obj != 42

    def test_alias_equal_to_canonical(self, ns):
        ns["owner"] = [10, 20]
        owner = ns.get_object("owner")
        ns["alias"] = owner
        assert ns.get_object("alias") == ns.get_object("owner")


class TestUnhashable:
    def test_list_object_unhashable(self, ns):
        ns["lst"] = [1]
        with pytest.raises(TypeError):
            hash(ns.get_object("lst"))

    def test_dict_object_unhashable(self, ns):
        ns["d"] = {"a": 1}
        with pytest.raises(TypeError):
            hash(ns.get_object("d"))

    def test_set_object_unhashable(self, ns):
        ns["s"] = {1}
        with pytest.raises(TypeError):
            hash(ns.get_object("s"))

    def test_scalar_object_unhashable(self, ns):
        ns["x"] = 42
        with pytest.raises(TypeError):
            hash(ns.get_object("x"))
