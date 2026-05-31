from __future__ import annotations

import pytest
from gpdatacached import TupleObject


class TestTupleObjectViaNamespace:
    def test_assign_and_read_tuple(self, ns):
        ns["t"] = (1, "x", 3.14)
        assert ns["t"] == (1, "x", 3.14)

    def test_get_object_returns_tuple_object(self, ns):
        ns["t"] = (1, "x", 3.14)
        obj = ns.get_object("t")
        assert isinstance(obj, TupleObject)
        assert obj.type == "scalar:tuple"
        assert obj.value == (1, "x", 3.14)

    def test_get_object_name_and_uri(self, ns):
        ns["mytuple"] = (1, 2)
        obj = ns.get_object("mytuple")
        assert obj.name == "mytuple"
        assert obj.uri == "gpdc://testdomain/testns/mytuple"

    def test_empty_tuple_as_value(self, ns):
        ns["empty"] = ()
        assert ns["empty"] == ()

    def test_empty_tuple_get_object(self, ns):
        ns["empty"] = ()
        obj = ns.get_object("empty")
        assert isinstance(obj, TupleObject)
        assert obj.value == ()

    def test_empty_tuple_in_list(self, ns):
        ns["lst"] = [1, (), 3]
        lst = ns.get_object("lst")
        assert lst.value == [1, (), 3]

    def test_empty_tuple_as_dict_value(self, ns):
        ns["d"] = {"a": ()}
        d = ns.get_object("d")
        assert d["a"] == ()

    def test_nonempty_tuple_as_dict_key(self, ns):
        ns["d"] = {(1, 2): "val"}
        d = ns.get_object("d")
        assert d[(1, 2)] == "val"

    def test_value_setter_updates_tuple(self, ns):
        ns["t"] = (1, 2, 3)
        obj = ns.get_object("t")
        obj.value = (4, 5, 6)
        assert obj.value == (4, 5, 6)
        assert ns["t"] == (4, 5, 6)

    def test_value_setter_rejects_wrong_type(self, ns):
        ns["t"] = (1, 2)
        obj = ns.get_object("t")
        with pytest.raises(TypeError, match="Cannot assign"):
            obj.value = [1, 2]


class TestEmptyTupleAsKey:
    def test_empty_tuple_as_dict_key_rejected(self, ns):
        ns["d"] = {"a": 1}
        d = ns.get_object("d")
        with pytest.raises(TypeError, match="empty tuple"):
            d[()] = "value"

    def test_empty_tuple_as_set_element_rejected(self, ns):
        ns["s"] = {1, 2}
        s = ns.get_object("s")
        with pytest.raises(TypeError, match="empty tuple"):
            s.add(())
