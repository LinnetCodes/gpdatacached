# tests/test_base.py
"""Tests for objects.py (GPDCDataObject base class) and __init__.py re-exports."""

from gpdatacached.objects import GPDCDataObject


def test_base_module_exports_gpdcdataobject():
    assert GPDCDataObject is not None
    assert hasattr(GPDCDataObject, "name")
    assert hasattr(GPDCDataObject, "namespace")
    assert hasattr(GPDCDataObject, "domain")
    assert hasattr(GPDCDataObject, "uri")
    assert hasattr(GPDCDataObject, "type")
    assert hasattr(GPDCDataObject, "value")


def test_package_reexports():
    import gpdatacached as pkg

    assert pkg.GPDCDataObject is GPDCDataObject
    assert issubclass(pkg.IntObject, GPDCDataObject)
    assert issubclass(pkg.ListObject, GPDCDataObject)
    assert issubclass(pkg.GPDCScalarObject, GPDCDataObject)
    assert issubclass(pkg.GPDCContainerObject, GPDCDataObject)
    assert issubclass(pkg.FloatObject, GPDCDataObject)
    assert issubclass(pkg.StrObject, GPDCDataObject)
    assert issubclass(pkg.BoolObject, GPDCDataObject)
    assert issubclass(pkg.DateTimeObject, GPDCDataObject)
    assert issubclass(pkg.DateObject, GPDCDataObject)
    assert issubclass(pkg.TimeObject, GPDCDataObject)
    assert issubclass(pkg.NoneObject, GPDCDataObject)
    assert issubclass(pkg.DictObject, GPDCDataObject)
    assert issubclass(pkg.SetObject, GPDCDataObject)


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
