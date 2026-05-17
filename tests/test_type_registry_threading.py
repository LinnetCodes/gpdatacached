import threading
import pytest
from gpdatacached.types import GPDCTypeRegistry


def test_lookup_by_gpdc_type_returns_correct_entry():
    entry = GPDCTypeRegistry.lookup_by_gpdc_type("scalar:int")
    assert entry.gpdc_type == "scalar:int"
    assert entry.object_class is not None


def test_lookup_by_gpdc_type_raises_on_unknown():
    with pytest.raises(ValueError, match="Unknown GPDC type"):
        GPDCTypeRegistry.lookup_by_gpdc_type("nonexistent:type")


def test_lookup_by_python_type_returns_correct_entry():
    entry = GPDCTypeRegistry.lookup_by_python_type(42)
    assert entry.gpdc_type == "scalar:int"


def test_lookup_by_python_type_bool_has_priority():
    entry = GPDCTypeRegistry.lookup_by_python_type(True)
    assert entry.gpdc_type == "scalar:bool"


def test_lookup_by_python_type_raises_on_unsupported():
    class CustomClass:
        pass

    with pytest.raises(TypeError, match="Unsupported Python type"):
        GPDCTypeRegistry.lookup_by_python_type(CustomClass())


def test_concurrent_registrations_do_not_corrupt():
    errors = []

    def register_and_lookup(idx):
        try:
            for _ in range(50):
                entry = GPDCTypeRegistry.lookup_by_python_type(idx)
                assert entry is not None
                entry = GPDCTypeRegistry.lookup_by_gpdc_type("scalar:int")
                assert entry is not None
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=register_and_lookup, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Concurrent access errors: {errors}"
