# tests/test_base.py
"""Tests for _base.py module and objects.py re-exports (Phase 1 circular import resolution)."""


def test_base_module_exports_gpdcdataobject():
    from gpdatacached._base import GPDCDataObject

    assert GPDCDataObject is not None
    assert hasattr(GPDCDataObject, "name")
    assert hasattr(GPDCDataObject, "namespace")
    assert hasattr(GPDCDataObject, "domain")
    assert hasattr(GPDCDataObject, "uri")
    assert hasattr(GPDCDataObject, "type")
    assert hasattr(GPDCDataObject, "value")


def test_objects_module_reexports():
    import gpdatacached.objects as obj_mod
    from gpdatacached._base import GPDCDataObject as BaseClass

    assert obj_mod.GPDCDataObject is BaseClass
    assert issubclass(obj_mod.IntObject, BaseClass)
    assert issubclass(obj_mod.ListObject, BaseClass)
    assert issubclass(obj_mod.GPDCScalarObject, BaseClass)
    assert issubclass(obj_mod.GPDCContainerObject, BaseClass)
    assert issubclass(obj_mod.FloatObject, BaseClass)
    assert issubclass(obj_mod.StrObject, BaseClass)
    assert issubclass(obj_mod.BoolObject, BaseClass)
    assert issubclass(obj_mod.DateTimeObject, BaseClass)
    assert issubclass(obj_mod.DateObject, BaseClass)
    assert issubclass(obj_mod.TimeObject, BaseClass)
    assert issubclass(obj_mod.NoneObject, BaseClass)
    assert issubclass(obj_mod.DictObject, BaseClass)
    assert issubclass(obj_mod.SetObject, BaseClass)
