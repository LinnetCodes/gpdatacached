def test_anonymous_list_created_correctly(ns):
    ns["parent"] = {"items": [1, 2, 3]}
    d = ns.get_object("parent")
    result = d["items"]
    assert isinstance(result.value, list)
    assert result.value == [1, 2, 3]


def test_anonymous_dict_created_correctly(ns):
    ns["parent"] = {"nested": {"a": 1}}
    d = ns.get_object("parent")
    nested = d["nested"]
    assert isinstance(nested.value, dict)
    assert nested.value == {"a": 1}


def test_anonymous_set_created_correctly(ns):
    ns["parent"] = {"items": {1, 2, 3}}
    d = ns.get_object("parent")
    items = d["items"]
    assert isinstance(items.value, set)
    assert items.value == {1, 2, 3}


def test_anonymous_nested_list_in_list(ns):
    ns["parent"] = [[1, 2], [3, 4]]
    lst = ns.get_object("parent")
    assert isinstance(lst.value, list)
    assert len(lst.value) == 2
