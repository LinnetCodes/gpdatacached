import pytest


class TestDomainRepr:
    def test_repr(self, domain):
        assert repr(domain) == "GPDCDomain('testdomain')"

    def test_str_delegates_to_repr(self, domain):
        assert str(domain) == repr(domain)


class TestNamespaceRepr:
    def test_repr(self, ns):
        assert repr(ns) == "GPDCNamespace('testns', domain='testdomain')"

    def test_str_delegates_to_repr(self, ns):
        assert str(ns) == repr(ns)


class TestScalarObjectRepr:
    @pytest.mark.parametrize(
        "py_value, expected_type",
        [(42, "IntObject"), ("hello", "StrObject")],
    )
    def test_repr(self, ns, py_value, expected_type):
        ns["x"] = py_value
        obj = ns.get_object("x")
        r = repr(obj)
        assert r == f"{expected_type}(uri='gpdc://testdomain/testns/x')"


class TestContainerObjectRepr:
    def test_list_repr(self, ns):
        ns["mylist"] = [1, 2, 3]
        obj = ns.get_object("mylist")
        assert repr(obj) == "ListObject(uri='gpdc://testdomain/testns/mylist')"

    def test_dict_repr(self, ns):
        ns["mydict"] = {"a": 1}
        obj = ns.get_object("mydict")
        assert repr(obj) == "DictObject(uri='gpdc://testdomain/testns/mydict')"

    def test_set_repr(self, ns):
        ns["myset"] = {1, 2, 3}
        obj = ns.get_object("myset")
        assert repr(obj) == "SetObject(uri='gpdc://testdomain/testns/myset')"


class TestAliasRepr:
    def test_alias_shows_alias_name(self, ns):
        ns["owner"] = [10, 20]
        owner = ns.get_object("owner")
        ns["alias"] = owner
        alias = ns.get_object("alias")
        assert repr(alias) == "ListObject(uri='gpdc://testdomain/testns/alias')"
        assert repr(owner) == "ListObject(uri='gpdc://testdomain/testns/owner')"
