# tests/test_base.py
"""Tests for _base.py module and objects.py re-exports (Phase 1 circular import resolution)."""

from gpdatacached._base import GPDCDataObject


def test_base_module_exports_gpdcdataobject():
    assert GPDCDataObject is not None
    assert hasattr(GPDCDataObject, "name")
    assert hasattr(GPDCDataObject, "namespace")
    assert hasattr(GPDCDataObject, "domain")
    assert hasattr(GPDCDataObject, "uri")
    assert hasattr(GPDCDataObject, "type")
    assert hasattr(GPDCDataObject, "value")


def test_objects_module_reexports():
    import gpdatacached.objects as obj_mod

    assert obj_mod.GPDCDataObject is GPDCDataObject
    assert issubclass(obj_mod.IntObject, GPDCDataObject)
    assert issubclass(obj_mod.ListObject, GPDCDataObject)
    assert issubclass(obj_mod.GPDCScalarObject, GPDCDataObject)
    assert issubclass(obj_mod.GPDCContainerObject, GPDCDataObject)
    assert issubclass(obj_mod.FloatObject, GPDCDataObject)
    assert issubclass(obj_mod.StrObject, GPDCDataObject)
    assert issubclass(obj_mod.BoolObject, GPDCDataObject)
    assert issubclass(obj_mod.DateTimeObject, GPDCDataObject)
    assert issubclass(obj_mod.DateObject, GPDCDataObject)
    assert issubclass(obj_mod.TimeObject, GPDCDataObject)
    assert issubclass(obj_mod.NoneObject, GPDCDataObject)
    assert issubclass(obj_mod.DictObject, GPDCDataObject)
    assert issubclass(obj_mod.SetObject, GPDCDataObject)


class TestDualIdentity:
    """Test dual-identity properties on GPDCDataObject."""

    def test_owner_handle_defaults(self, ns):
        """Owner handle: canonical_name == name, is_reference == False."""
        ns["mylist"] = [1, 2, 3]
        obj = ns.get_object("mylist")
        assert obj.name == "mylist"
        assert obj.canonical_name == "mylist"
        assert obj.is_reference is False
        assert obj.uri == obj.canonical_uri

    def test_alias_handle_identity(self, ns):
        """Alias handle preserves alias name, exposes canonical name."""
        ns["owner"] = [10, 20]
        ns["alias"] = ns["owner"]
        obj = ns.get_object("alias")
        assert obj.name == "alias"
        assert obj.canonical_name == "owner"
        assert obj.is_reference is True
        assert "alias" in obj.uri
        assert "owner" in obj.canonical_uri
        assert obj.uri != obj.canonical_uri

    def test_scalar_is_never_reference(self, ns):
        """Scalar objects are always canonical owners."""
        ns["x"] = 42
        obj = ns.get_object("x")
        assert obj.is_reference is False
        assert obj.canonical_name == "x"
