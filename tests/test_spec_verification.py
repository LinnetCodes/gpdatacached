import datetime

import pytest

from gpdatacached import GPDCNamespace, ListObject, __version__
from gpdatacached.exceptions import CanonicalObjectReferencedError
from gpdatacached.keys import GLOBAL_META_KEY


class TestSpecVerification:
    """Spec verification tests"""

    def test_domain_ops(self, domain, clean_redis):
        ns = domain.get_namespace("spec_verification")
        test_ns = domain.get_namespace("test_ns")

        assert clean_redis.hget(GLOBAL_META_KEY, "version") == __version__
        assert "testdomain" in clean_redis.smembers("gpdc:__domains__")
        assert clean_redis.hget("gpdc:testdomain:__meta__", "default_cache_mode") == "permanent"
        assert clean_redis.hget("gpdc:testdomain:__meta__", "default_ttl_seconds") == "0"
        assert "spec_verification" in clean_redis.smembers("gpdc:testdomain:__namespaces__")
        assert clean_redis.hget("gpdc:testdomain:spec_verification:__meta__", "default_cache_mode") == "permanent"
        assert clean_redis.hget("gpdc:testdomain:spec_verification:__meta__", "default_ttl_seconds") == "0"

        assert domain.name == "testdomain"
        assert "spec_verification" in domain
        assert "test_ns" in domain
        assert len(domain) == 2
        for name in domain:
            assert name in {"spec_verification", "test_ns"}
        assert ns is domain["spec_verification"]
        assert isinstance(domain["test_ns"], GPDCNamespace)
        assert isinstance(test_ns, GPDCNamespace)
        assert isinstance(ns, GPDCNamespace)

    def test_namespace_ops(self, domain, clean_redis):
        ns = domain.get_namespace("spec_verification")
        test_ns = domain.get_namespace("test_ns")

        ns["test_int"] = 42
        ns["test_float"] = 42.2
        ns["test_bool"] = True
        ns["test_str"] = "hello, world"
        ns["test_datetime"] = datetime.datetime(2020, 1, 2)
        ns["test_date"] = datetime.date(2020, 1, 1)
        ns["test_time"] = datetime.time(0, 0, 0)
        ns["test_none"] = None

        assert len(ns) == 8
        for name in ns:
            assert name in {
                "test_int",
                "test_float",
                "test_bool",
                "test_str",
                "test_datetime",
                "test_date",
                "test_time",
                "test_none",
            }
        for name in {
            "test_int",
            "test_float",
            "test_bool",
            "test_str",
            "test_datetime",
            "test_date",
            "test_time",
            "test_none",
        }:
            assert name in ns
        assert ns["test_int"] == 42
        assert ns["test_float"] == 42.2
        assert ns["test_bool"] is True
        assert ns["test_str"] == "hello, world"
        assert ns["test_datetime"] == datetime.datetime(2020, 1, 2)
        assert ns["test_date"] == datetime.date(2020, 1, 1)
        assert ns["test_time"] == datetime.time(0, 0, 0)
        assert ns["test_none"] is None

        test_ns.setdefault("test_setdefault", "hello, default")
        assert test_ns["test_setdefault"] == "hello, default"
        obj = test_ns.ensure_object("test_get", 100)
        assert obj.value == 100
        assert test_ns["test_get"] == 100

    # ── List Ops ──────────────────────────────────────────────────────

    def test_list_creation(self, domain, clean_redis):
        # ── §9.3 创建 List ──────────────────────────────────────────
        ns = domain.get_namespace("spec_verification")
        d = "testdomain"
        n = "spec_verification"

        ns["mylist"] = [1, 2, 3]

        obj_key = f"gpdc:{d}:{n}:obj:mylist"
        raw_key = f"gpdc:{d}:{n}:raw:mylist"
        ref_key = f"gpdc:{d}:{n}:ref:mylist"

        assert clean_redis.hget(obj_key, "type") == "collection:list"
        assert clean_redis.hget(obj_key, "value") == raw_key
        assert clean_redis.hget(obj_key, "cache_mode") == "permanent"
        assert clean_redis.hget(obj_key, "ttl") == ""
        assert clean_redis.hget(obj_key, "created_at") is not None
        assert clean_redis.hget(obj_key, "updated_at") is not None

        assert clean_redis.exists(raw_key)
        assert clean_redis.lrange(raw_key, 0, -1) == ["int:1", "int:2", "int:3"]

        assert clean_redis.smembers(ref_key) == set()

    def test_list_type_identity(self, domain, clean_redis):
        # ── §2.1 类型标识 ───────────────────────────────────────────
        from gpdatacached import ListObject

        ns = domain.get_namespace("spec_verification")

        ns["mylist"] = [1, 2, 3]

        lst = ns["mylist"]
        assert isinstance(lst, ListObject)
        assert lst.type == "collection:list"

    def test_list_container_object_attrs(self, domain, clean_redis):
        # ── §8 ContainerObject 属性 ─────────────────────────────────
        ns = domain.get_namespace("spec_verification")
        d = "testdomain"
        n = "spec_verification"

        ns["mylist"] = [1, 2, 3]
        raw_key = f"gpdc:{d}:{n}:raw:mylist"
        ref_key = f"gpdc:{d}:{n}:ref:mylist"

        lst = ns["mylist"]
        assert lst.raw_key == raw_key
        assert lst.ref_key == ref_key
        assert lst.referrers() == set()

    def test_list_mutable_sequence_methods(self, domain, clean_redis):
        # ── §9.1 MutableSequence 方法 ───────────────────────────────
        ns = domain.get_namespace("spec_verification")
        d = "testdomain"
        n = "spec_verification"

        ns["mylist"] = [1, 2, 3]
        lst = ns["mylist"]
        raw_key = f"gpdc:{d}:{n}:raw:mylist"

        assert clean_redis.lrange(raw_key, 0, -1) == ["int:1", "int:2", "int:3"]
        assert len(lst) == 3
        assert lst[0] == 1
        assert lst[1] == 2
        assert lst[2] == 3
        assert lst[-1] == 3
        assert 2 in lst
        assert 99 not in lst
        assert list(lst) == [1, 2, 3]
        assert lst.count(1) == 1
        assert lst.count(99) == 0
        assert lst.index(2) == 1

        lst.append(4)
        assert clean_redis.lrange(raw_key, 0, -1) == [
            "int:1",
            "int:2",
            "int:3",
            "int:4",
        ]

        lst.extend([5, 6])
        assert clean_redis.lrange(raw_key, 0, -1) == [
            "int:1",
            "int:2",
            "int:3",
            "int:4",
            "int:5",
            "int:6",
        ]

        lst[0] = 10
        assert clean_redis.lrange(raw_key, 0, -1) == [
            "int:10",
            "int:2",
            "int:3",
            "int:4",
            "int:5",
            "int:6",
        ]

        lst.pop()
        assert clean_redis.lrange(raw_key, 0, -1) == [
            "int:10",
            "int:2",
            "int:3",
            "int:4",
            "int:5",
        ]

        lst.pop(0)
        assert clean_redis.lrange(raw_key, 0, -1) == [
            "int:2",
            "int:3",
            "int:4",
            "int:5",
        ]

        lst.insert(0, 1)
        assert clean_redis.lrange(raw_key, 0, -1) == [
            "int:1",
            "int:2",
            "int:3",
            "int:4",
            "int:5",
        ]

        lst.remove(3)
        assert clean_redis.lrange(raw_key, 0, -1) == [
            "int:1",
            "int:2",
            "int:4",
            "int:5",
        ]

        del lst[0]
        assert clean_redis.lrange(raw_key, 0, -1) == [
            "int:2",
            "int:4",
            "int:5",
        ]

        lst.reverse()
        assert clean_redis.lrange(raw_key, 0, -1) == [
            "int:5",
            "int:4",
            "int:2",
        ]

        lst.sort()
        assert clean_redis.lrange(raw_key, 0, -1) == [
            "int:2",
            "int:4",
            "int:5",
        ]

        lst.clear()
        assert clean_redis.lrange(raw_key, 0, -1) == []

    def test_list_container_ref_sharing(self, domain, clean_redis):
        # ── §6.2 容器赋值 — 引用共享 ────────────────────────────────
        ns = domain.get_namespace("spec_verification")
        d = "testdomain"
        n = "spec_verification"

        ns["a"] = [1, 2, 3]
        ns["b"] = ns["a"]

        obj_a = f"gpdc:{d}:{n}:obj:a"
        obj_b = f"gpdc:{d}:{n}:obj:b"
        ref_a = f"gpdc:{d}:{n}:ref:a"

        assert clean_redis.type(obj_b) == "string"
        assert clean_redis.get(obj_b) == f"ref:{obj_a}"
        assert "b" in clean_redis.smembers(ref_a)
        assert "b" in ns["a"].referrers()

        ns["b"].append(4)
        assert list(ns["a"]) == [1, 2, 3, 4]

    def test_list_scalar_value_copy(self, domain, clean_redis):
        # ── §6.1 标量赋值 — 值复制 ─────────────────────────────────
        ns = domain.get_namespace("spec_verification")

        ns["s1"] = 10
        ns["s2"] = ns["s1"]
        assert ns["s1"] == 10
        assert ns["s2"] == 10
        ns["s2"] = 20
        assert ns["s1"] == 10

    def test_list_reassign_ref_maintenance(self, domain, clean_redis):
        # ── §6.3 重新赋值的引用维护 ──────────────────────────────────
        ns = domain.get_namespace("spec_verification")
        d = "testdomain"
        n = "spec_verification"

        ns["x"] = [1, 2, 3]
        ns["y"] = ns["x"]
        ref_x = f"gpdc:{d}:{n}:ref:x"
        assert "y" in clean_redis.smembers(ref_x)

        ns["y"] = [4, 5, 6]
        assert "y" not in clean_redis.smembers(ref_x)
        assert list(ns["x"]) == [1, 2, 3]
        assert list(ns["y"]) == [4, 5, 6]
        raw_y = f"gpdc:{d}:{n}:raw:y"
        assert clean_redis.exists(raw_y)

    def test_list_owner_overwrite_allowed_without_refs(self, domain, clean_redis):
        ns = domain.get_namespace("spec_verification")

        ns["x"] = [1, 2, 3]
        ns["x"] = 42
        assert ns["x"] == 42

    def test_list_owner_overwrite_blocked_with_refs(self, domain, clean_redis):
        ns = domain.get_namespace("spec_verification")

        ns["x"] = [1, 2, 3]
        ns["y"] = ns["x"]

        with pytest.raises(CanonicalObjectReferencedError):
            ns["x"] = 42

    def test_list_copy_deepcopy(self, domain, clean_redis):
        # ── §7 copy / deepcopy ──────────────────────────────────────
        ns = domain.get_namespace("spec_verification")
        d = "testdomain"
        n = "spec_verification"

        ns["src"] = [1, [4, 5], 3]

        # copy() — shallow copy: nested container references are shared
        ns["c1"] = ns["src"].copy()
        ns["c1"][1].append(6)
        assert list(ns["src"][1]) == [4, 5, 6]
        assert list(ns["c1"][1]) == [4, 5, 6]

        # deepcopy() — deep copy: nested containers are fully independent
        ns["src2"] = [1, [4, 5], 3]
        ns["c2"] = ns["src2"].deepcopy()
        ns["c2"][1].append(6)
        assert list(ns["src2"][1]) == [4, 5]
        assert list(ns["c2"][1]) == [4, 5, 6]

        # both: top-level elements are independent of source
        ns["c1"].append(10)
        assert ns["src"][0] == 1
        assert isinstance(ns["src"][1], ListObject)
        assert isinstance(ns["c1"][1], ListObject)
        assert ns["c1"][1][1] == 5
        assert ns["src"][1][1] == 5
        assert list(ns["src"][1]) == [4, 5, 6]
        assert ns["src"][2] == 3
        assert len(ns["src"]) == 3
        assert len(ns["c1"]) == 4
        assert ns["c1"][3] == 10
        ns["c2"].append(10)
        assert ns["src2"][0] == 1
        assert list(ns["src2"][1]) == [4, 5]
        assert ns["src2"][2] == 3
        assert len(ns["src2"]) == 3
        assert len(ns["c2"]) == 4

        ref_src = f"gpdc:{d}:{n}:ref:src"
        assert "c1" not in clean_redis.smembers(ref_src)

        ref_src2 = f"gpdc:{d}:{n}:ref:src2"
        assert "c2" not in clean_redis.smembers(ref_src2)

        raw_c1 = f"gpdc:{d}:{n}:raw:c1"
        raw_c2 = f"gpdc:{d}:{n}:raw:c2"
        obj_c1 = f"gpdc:{d}:{n}:obj:c1"
        obj_c2 = f"gpdc:{d}:{n}:obj:c2"
        assert clean_redis.hget(obj_c1, "value") == raw_c1
        assert clean_redis.hget(obj_c2, "value") == raw_c2

    def test_list_delete_no_refs(self, domain, clean_redis):
        # ── §10.1 删除无引用的容器对象 ──────────────────────────────
        ns = domain.get_namespace("spec_verification")
        d = "testdomain"
        n = "spec_verification"

        ns["alone"] = [1, 2]
        del ns["alone"]
        assert not clean_redis.exists(f"gpdc:{d}:{n}:obj:alone")
        assert not clean_redis.exists(f"gpdc:{d}:{n}:raw:alone")
        assert not clean_redis.exists(f"gpdc:{d}:{n}:ref:alone")

    def test_list_delete_with_refs_raises_error(self, domain, clean_redis):
        ns = domain.get_namespace("spec_verification")

        ns["owner"] = [10, 20]
        ns["sharer"] = ns["owner"]
        with pytest.raises(CanonicalObjectReferencedError):
            del ns["owner"]

    def test_list_delete_referrer(self, domain, clean_redis):
        # ── §10.1 删除引用者（从原 ref set 移除） ───────────────────
        ns = domain.get_namespace("spec_verification")
        d = "testdomain"
        n = "spec_verification"

        ns["p"] = [1, 2, 3]
        ns["q"] = ns["p"]
        ref_p = f"gpdc:{d}:{n}:ref:p"
        assert "q" in clean_redis.smembers(ref_p)
        del ns["q"]
        assert "q" not in clean_redis.smembers(ref_p)
        assert list(ns["p"]) == [1, 2, 3]

    def test_list_write_updates_timestamp(self, domain, clean_redis):
        # ── §9.2 写操作更新 updated_at ──────────────────────────────
        import time

        ns = domain.get_namespace("spec_verification")
        d = "testdomain"
        n = "spec_verification"

        ns["touch_test"] = [1]
        obj_touch = f"gpdc:{d}:{n}:obj:touch_test"
        before = clean_redis.hget(obj_touch, "updated_at")
        time.sleep(0.01)
        ns["touch_test"].append(2)
        after = clean_redis.hget(obj_touch, "updated_at")
        assert after >= before

    def test_list_scalar_element_encoding(self, domain, clean_redis):
        # ── §3 标量元素编码（多种类型） ──────────────────────────────
        import datetime

        ns = domain.get_namespace("spec_verification")

        ns["mixed"] = [42, 1.5, True, "hello", None, datetime.date(2020, 1, 1)]
        mixed_lst = ns["mixed"]
        assert mixed_lst[0] == 42
        assert mixed_lst[1] == 1.5
        assert mixed_lst[2] is True
        assert mixed_lst[3] == "hello"
        assert mixed_lst[4] is None
        assert mixed_lst[5] == datetime.date(2020, 1, 1)

    # ── Dict Ops ──────────────────────────────────────────────────────

    def test_dict_creation_metadata(self, domain, clean_redis):
        # ── §2 DictObject 类型标识与元数据 ────────────────────────────
        ns = domain.get_namespace("spec_verification")
        d = "testdomain"
        n = "spec_verification"

        ns["mydict"] = {"name": "test"}

        obj_key = f"gpdc:{d}:{n}:obj:mydict"
        raw_key = f"gpdc:{d}:{n}:raw:mydict"
        ref_key = f"gpdc:{d}:{n}:ref:mydict"

        assert clean_redis.hget(obj_key, "type") == "collection:dict"
        assert clean_redis.hget(obj_key, "value") == raw_key
        assert clean_redis.hget(obj_key, "cache_mode") == "permanent"
        assert clean_redis.hget(obj_key, "ttl") == ""
        assert clean_redis.hget(obj_key, "created_at") is not None
        assert clean_redis.hget(obj_key, "updated_at") is not None

        assert clean_redis.hget(raw_key, "str:name") == "str:test"
        assert clean_redis.smembers(ref_key) == set()

    def test_dict_type_identity(self, domain, clean_redis):
        # ── §2.1 类型标识 ────────────────────────────────────────────
        from gpdatacached import DictObject

        ns = domain.get_namespace("spec_verification")

        ns["mydict"] = {"name": "test"}

        dct = ns["mydict"]
        assert isinstance(dct, DictObject)
        assert dct.type == "collection:dict"

    def test_dict_mutable_mapping_methods(self, domain, clean_redis):
        # ── §9 DictObject MutableMapping 方法 ─────────────────────────
        ns = domain.get_namespace("spec_verification")

        ns["mydict"] = {"name": "test"}
        dct = ns["mydict"]

        assert len(dct) == 1
        assert dct["name"] == "test"
        assert "name" in dct
        assert "missing" not in dct
        assert set(dct) == {"name"}
        assert set(dct.keys()) == {"name"}
        assert dct.values() == ["test"]
        assert dct.items() == [("name", "test")]

        dct["age"] = 30
        assert dct["age"] == 30
        assert len(dct) == 2

        dct["age"] = 31
        assert dct["age"] == 31

        assert dct.get("name") == "test"
        assert dct.get("missing") is None
        assert dct.get("missing", "fallback") == "fallback"

        val = dct.pop("age")
        assert val == 31
        assert "age" not in dct
        assert dct.pop("age", 99) == 99

        dct.setdefault("city", "NYC")
        assert dct["city"] == "NYC"
        dct.setdefault("city", "LA")
        assert dct["city"] == "NYC"

        dct.update({"x": 1, "y": 2})
        assert dct["x"] == 1
        assert dct["y"] == 2

        dct.clear()
        assert len(dct) == 0
        assert dict(dct.items()) == {}

    def test_dict_tuple_key_encoding(self, domain, clean_redis):
        # ── §4 Tuple Key 编码规则 ─────────────────────────────────────
        from gpdatacached.codec import TupleCodec

        ns = domain.get_namespace("spec_verification")
        d = "testdomain"
        n = "spec_verification"

        # §4.2 编码示例
        assert TupleCodec.encode_value(("a", "b")) == 'tuple:["str:a","str:b"]'
        assert TupleCodec.encode_value(("a,b", "c")) == 'tuple:["str:a,b","str:c"]'
        assert TupleCodec.encode_value((1, "x", 2.5)) == 'tuple:["int:1","str:x","float:2.5"]'
        assert TupleCodec.encode_value((True, None)) == 'tuple:["bool:True","none:None"]'

        # §4.3 roundtrip
        assert TupleCodec.decode_value('tuple:["str:a","str:b"]') == ("a", "b")
        assert TupleCodec.decode_value('tuple:["int:1","str:x","float:2.5"]') == (1, "x", 2.5)

        # §4.4 Dict Hash 字段编码完整示例
        ns["td"] = {"text": "test", 2: 100, ("a", "b"): "example"}
        td_raw = f"gpdc:{d}:{n}:raw:td"
        assert clean_redis.hget(td_raw, "str:text") == "str:test"
        assert clean_redis.hget(td_raw, "int:2") == "int:100"
        assert clean_redis.hget(td_raw, 'tuple:["str:a","str:b"]') == "str:example"

        td = ns["td"]
        assert td["text"] == "test"
        assert td[2] == 100
        assert td[("a", "b")] == "example"
        assert ("a", "b") in td
        del td[("a", "b")]
        assert ("a", "b") not in td

    def test_dict_element_encoding(self, domain, clean_redis):
        # ── §3 容器元素编码规则 ────────────────────────────────────────
        from gpdatacached import ListObject

        ns = domain.get_namespace("spec_verification")
        d = "testdomain"
        n = "spec_verification"

        # §3.1 标量元素编码
        ns["scalar_dict"] = {"i": 42, "f": 1.5, "b": True, "s": "hello", "n": None}
        sd = ns["scalar_dict"]
        assert sd["i"] == 42
        assert sd["f"] == 1.5
        assert sd["b"] is True
        assert sd["s"] == "hello"
        assert sd["n"] is None

        # §3.2 嵌套容器元素编码 — ref: 前缀
        ns["nest_dict"] = {"a": [1, 2, 3], "b": "hello"}
        nd_raw = f"gpdc:{d}:{n}:raw:nest_dict"
        assert clean_redis.hget(nd_raw, "str:a").startswith("ref:gpdc:")
        assert clean_redis.hget(nd_raw, "str:b") == "str:hello"

        # §3.3 解码判断逻辑
        nd = ns["nest_dict"]
        inner_list = nd["a"]
        assert isinstance(inner_list, ListObject)
        assert inner_list[0] == 1
        assert inner_list[1] == 2
        assert inner_list[2] == 3
        assert nd["b"] == "hello"

    def test_dict_anonymous_objects(self, domain, clean_redis):
        # ── §5 匿名对象 ──────────────────────────────────────────────
        ns = domain.get_namespace("spec_verification")
        d = "testdomain"
        n = "spec_verification"

        ns["nest_dict"] = {"a": [1, 2, 3], "b": "hello"}
        nd_raw = f"gpdc:{d}:{n}:raw:nest_dict"

        # §5.2 匿名对象命名 ~anon:<uuid>
        anon_ref = clean_redis.hget(nd_raw, "str:a")
        assert ":obj:~anon:" in anon_ref
        anon_name = anon_ref.split(":obj:")[1]
        assert anon_name.startswith("~anon:")

        # §5.3 匿名对象的元数据
        anon_obj_key = f"gpdc:{d}:{n}:obj:{anon_name}"
        assert clean_redis.hget(anon_obj_key, "type") == "collection:list"
        assert clean_redis.hget(anon_obj_key, "cache_mode") == "inherited"
        assert clean_redis.hget(anon_obj_key, "ttl") == ""
        assert clean_redis.hget(anon_obj_key, "created_at") is not None
        assert clean_redis.hget(anon_obj_key, "updated_at") is not None

        anon_raw_key = f"gpdc:{d}:{n}:raw:{anon_name}"
        assert clean_redis.exists(anon_raw_key)
        assert clean_redis.lrange(anon_raw_key, 0, -1) == ["int:1", "int:2", "int:3"]

        # §5.3 ref set 包含父对象名称
        anon_ref_key = f"gpdc:{d}:{n}:ref:{anon_name}"
        assert "nest_dict" in clean_redis.smembers(anon_ref_key)

        # §5.4 匿名对象 cache_mode 继承
        assert clean_redis.hget(anon_obj_key, "cache_mode") == "inherited"

        # §5.5 匿名对象不出现在 namespace 迭代中
        for name in ns:
            assert not name.startswith("~")

        # §5.5 匿名对象不能通过 ns["~anon:xxx"] 直接访问
        with pytest.raises(ValueError):
            ns[anon_name]

    def test_dict_nesting_full_flow(self, domain, clean_redis):
        # ── §6 容器嵌套完整流程 ──────────────────────────────────────
        from gpdatacached import DictObject, ListObject

        ns = domain.get_namespace("spec_verification")
        d = "testdomain"
        n = "spec_verification"

        # §6.1 创建嵌套容器
        ns["mydict"] = {"a": [1, 2, 3], "b": {"x": 1}, "c": 42}
        md_raw = f"gpdc:{d}:{n}:raw:mydict"
        assert clean_redis.hget(md_raw, "str:a").startswith("ref:gpdc:")
        assert clean_redis.hget(md_raw, "str:b").startswith("ref:gpdc:")
        assert clean_redis.hget(md_raw, "str:c") == "int:42"

        # §6.2 读取嵌套容器
        md = ns["mydict"]
        assert md["c"] == 42
        assert isinstance(md["a"], ListObject)
        assert md["a"][0] == 1
        assert isinstance(md["b"], DictObject)
        assert md["b"]["x"] == 1

        # §6.3 List 中的嵌套
        ns["mylist"] = [{"name": "Alice"}, {"name": "Bob"}]
        ml_raw = f"gpdc:{d}:{n}:raw:mylist"
        ml_items = clean_redis.lrange(ml_raw, 0, -1)
        assert ml_items[0].startswith("ref:gpdc:")
        assert ml_items[1].startswith("ref:gpdc:")

        ml = ns["mylist"]
        assert isinstance(ml[0], DictObject)
        assert ml[0]["name"] == "Alice"
        assert isinstance(ml[1], DictObject)
        assert ml[1]["name"] == "Bob"

        # 验证匿名 dict 的 raw 数据
        anon0_name = ml_items[0].split(":obj:")[1]
        anon0_raw = f"gpdc:{d}:{n}:raw:{anon0_name}"
        assert clean_redis.hget(anon0_raw, "str:name") == "str:Alice"
        anon0_ref = f"gpdc:{d}:{n}:ref:{anon0_name}"
        assert "mylist" in clean_redis.smembers(anon0_ref)

        # ── §6 深层嵌套 ─────────────────────────────────────────────
        ns["deep"] = {"a": {"b": {"c": 42}}}
        dp = ns["deep"]
        assert dp["a"]["b"]["c"] == 42

        ns["deep_list"] = [{"items": [1, 2, 3]}]
        dl = ns["deep_list"]
        assert isinstance(dl[0], DictObject)
        assert isinstance(dl[0]["items"], ListObject)
        assert list(dl[0]["items"]) == [1, 2, 3]

    def test_dict_runtime_nesting(self, domain, clean_redis):
        # ── §9.3 value 编解码（运行时嵌套） ───────────────────────────
        from gpdatacached import DictObject, ListObject

        ns = domain.get_namespace("spec_verification")

        ns["runtime_nest"] = {"a": 1}
        rn = ns["runtime_nest"]
        rn["b"] = [4, 5, 6]
        assert isinstance(rn["b"], ListObject)
        assert list(rn["b"]) == [4, 5, 6]

        rn["c"] = {"x": 10}
        assert isinstance(rn["c"], DictObject)
        assert rn["c"]["x"] == 10

        # List 运行时追加嵌套
        ns["rn_list"] = [1, 2]
        rl = ns["rn_list"]
        rl.append({"name": "Alice"})
        assert isinstance(rl[2], DictObject)
        assert rl[2]["name"] == "Alice"

    def test_dict_copy_deepcopy_nesting(self, domain, clean_redis):
        # ── §8 copy() 与 deepcopy()（嵌套场景） ───────────────────────
        ns = domain.get_namespace("spec_verification")

        # §8.1 copy() — 浅拷贝
        ns["original"] = {"a": [1, 2, 3], "b": "hello"}
        ns["shallow"] = ns["original"].copy()
        ns["shallow"]["a"].append(4)
        assert list(ns["original"]["a"]) == [1, 2, 3, 4]
        ns["shallow"]["b"] = "world"
        assert ns["original"]["b"] == "hello"

        # §8.2 deepcopy() — 深拷贝
        ns["deepcopy_src"] = {"a": [1, 2, 3], "b": "hello"}
        ns["deep"] = ns["deepcopy_src"].deepcopy()
        ns["deep"]["a"].append(4)
        assert list(ns["deepcopy_src"]["a"]) == [1, 2, 3]

    def test_dict_delete_nested_containers(self, domain, clean_redis):
        # ── §10 删除嵌套容器 ─────────────────────────────────────────
        ns = domain.get_namespace("spec_verification")
        d = "testdomain"
        n = "spec_verification"

        # §10.1 递归删除匿名对象（无引用者时级联清理）
        ns["cascade"] = {"a": [1, 2, 3], "b": "hello"}
        ca_raw = f"gpdc:{d}:{n}:raw:cascade"
        anon_a_ref = clean_redis.hget(ca_raw, "str:a")
        anon_a_name = anon_a_ref.split(":obj:")[1]

        del ns["cascade"]
        assert not clean_redis.exists(f"gpdc:{d}:{n}:obj:cascade")
        assert not clean_redis.exists(f"gpdc:{d}:{n}:raw:cascade")
        assert not clean_redis.exists(f"gpdc:{d}:{n}:obj:{anon_a_name}")
        assert not clean_redis.exists(f"gpdc:{d}:{n}:raw:{anon_a_name}")

        # §10.2 — owner with referrers: deletion is blocked
        ns["x"] = {"a": [1, 2, 3]}
        ns["y"] = ns["x"]

        with pytest.raises(Exception):
            del ns["x"]
        del ns["y"]
        del ns["x"]
        assert not clean_redis.exists(f"gpdc:{d}:{n}:obj:x")
        assert not clean_redis.exists(f"gpdc:{d}:{n}:raw:x")

        # §10.1 Dict 引用共享
        ns["d_ref_a"] = {"x": 1}
        ns["d_ref_b"] = ns["d_ref_a"]
        ref_a = f"gpdc:{d}:{n}:ref:d_ref_a"
        assert "d_ref_b" in clean_redis.smembers(ref_a)
        ns["d_ref_b"]["y"] = 2
        assert ns["d_ref_a"]["y"] == 2

        # 删除引用共享的容器 — must delete referrer first
        del ns["d_ref_b"]
        del ns["d_ref_a"]
        assert not clean_redis.exists(f"gpdc:{d}:{n}:obj:d_ref_a")
        assert not clean_redis.exists(f"gpdc:{d}:{n}:raw:d_ref_a")

    def test_dict_write_updates_timestamp(self, domain, clean_redis):
        # ── §9.4 写操作更新 updated_at ───────────────────────────────
        import time

        ns = domain.get_namespace("spec_verification")
        d = "testdomain"
        n = "spec_verification"

        ns["touch_d"] = {"a": 1}
        obj_touch = f"gpdc:{d}:{n}:obj:touch_d"
        before = clean_redis.hget(obj_touch, "updated_at")
        time.sleep(0.01)
        ns["touch_d"]["b"] = 2
        after = clean_redis.hget(obj_touch, "updated_at")
        assert after >= before

    def test_dict_full_data_flow(self, domain, clean_redis):
        # ── §11 完整数据流示例 ───────────────────────────────────────
        from gpdatacached import ListObject

        ns = domain.get_namespace("spec_verification")

        ns["mydict"] = {"a": [1, 2, 3], "b": {"x": 1}, "c": 42}
        dd = ns["mydict"]
        assert dd["c"] == 42
        assert dd["a"][0] == 1
        assert dd["b"]["x"] == 1

        dd["d"] = [4, 5, 6]
        assert isinstance(dd["d"], ListObject)
        assert list(dd["d"]) == [4, 5, 6]

        dd["a"].append(4)
        assert list(dd["a"]) == [1, 2, 3, 4]

        dd[("key1", "key2")] = "value"
        assert dd[("key1", "key2")] == "value"

    # ── Set Ops ───────────────────────────────────────────────────────

    def test_set_creation_metadata(self, domain, clean_redis):
        # ── §2.1 类型标识 ─────────────────────────────────────────────
        ns = domain.get_namespace("spec_verification")
        d = "testdomain"
        n = "spec_verification"

        ns["myset"] = {1, 2, 3}

        obj_key = f"gpdc:{d}:{n}:obj:myset"
        raw_key = f"gpdc:{d}:{n}:raw:myset"
        ref_key = f"gpdc:{d}:{n}:ref:myset"

        assert clean_redis.hget(obj_key, "type") == "collection:set"
        assert clean_redis.hget(obj_key, "value") == raw_key
        assert clean_redis.hget(obj_key, "cache_mode") == "permanent"
        assert clean_redis.hget(obj_key, "ttl") == ""
        assert clean_redis.hget(obj_key, "created_at") is not None
        assert clean_redis.hget(obj_key, "updated_at") is not None

        # ── §2.3 Raw 数据格式 ─────────────────────────────────────────
        members = clean_redis.smembers(raw_key)
        assert members == {"int:1", "int:2", "int:3"}

        assert clean_redis.smembers(ref_key) == set()

    def test_set_object_hierarchy(self, domain, clean_redis):
        # ── §1.1 对象体系 — SetObject 是 GPDCContainerObject ──────────
        from gpdatacached import SetObject

        ns = domain.get_namespace("spec_verification")

        ns["myset"] = {1, 2, 3}
        s = ns["myset"]
        assert isinstance(s, SetObject)
        assert s.type == "collection:set"

    def test_set_container_base_attrs(self, domain, clean_redis):
        # ── §7.3 GPDCContainerObject 统一基类属性 ─────────────────────
        ns = domain.get_namespace("spec_verification")
        d = "testdomain"
        n = "spec_verification"

        ns["myset"] = {1, 2, 3}
        raw_key = f"gpdc:{d}:{n}:raw:myset"
        ref_key = f"gpdc:{d}:{n}:ref:myset"

        s = ns["myset"]
        assert s.raw_key == raw_key
        assert s.ref_key == ref_key
        assert s.referrers() == set()

    def test_set_standard_methods(self, domain, clean_redis):
        # ── §4.1 标准方法 ─────────────────────────────────────────────
        ns = domain.get_namespace("spec_verification")

        ns["myset"] = {1, 2, 3}
        s = ns["myset"]

        assert len(s) == 3
        assert 1 in s
        assert 2 in s
        assert 3 in s
        assert 99 not in s
        assert set(s) == {1, 2, 3}

        s.add(4)
        assert 4 in s
        assert len(s) == 4

        s.add(1)
        assert len(s) == 4

        s.discard(4)
        assert 4 not in s
        assert len(s) == 3

        s.discard(999)
        assert len(s) == 3

        s.remove(3)
        assert 3 not in s
        assert len(s) == 2

        with pytest.raises(KeyError):
            s.remove(999)

        popped = s.pop()
        assert popped in {1, 2}
        assert popped not in s
        assert len(s) == 1

        s.clear()
        assert len(s) == 0
        assert set(s) == set()

        with pytest.raises(KeyError):
            s.pop()

    def test_set_tuple_element_encoding(self, domain, clean_redis):
        # ── §2.5 Tuple 元素在 Set 中的编码 ───────────────────────────
        ns = domain.get_namespace("spec_verification")
        d = "testdomain"
        n = "spec_verification"

        ns["tset"] = {1, (2, 3)}
        tset_obj_key = f"gpdc:{d}:{n}:obj:tset"
        assert clean_redis.hget(tset_obj_key, "type") == "collection:set"

        tset_raw_key = f"gpdc:{d}:{n}:raw:tset"
        tset_members = clean_redis.smembers(tset_raw_key)
        assert "int:1" in tset_members
        assert any(m.startswith("tuple:") for m in tset_members)

        tset = ns["tset"]
        assert 1 in tset
        assert (2, 3) in tset
        assert set(tset) == {1, (2, 3)}

    def test_set_element_constraints(self, domain, clean_redis):
        # ── §2.4 Set 元素限制 — 标量和 tuple，混合标量 ──────────────
        ns = domain.get_namespace("spec_verification")

        ns["mixed_scalars"] = {1, "hello", 3.14}
        ms = ns["mixed_scalars"]
        assert 1 in ms
        assert "hello" in ms
        assert 3.14 in ms
        assert set(ms) == {1, "hello", 3.14}

    def test_set_codec_encoding_decoding(self, domain, clean_redis):
        # ── §3 SetCodec 编解码流程 ───────────────────────────────────
        from gpdatacached.types import encode_key, decode_key

        # 标量 → registry-driven encode_key
        assert encode_key(42) == "int:42"
        assert encode_key("abc") == "str:abc"
        # tuple → TupleCodec
        assert encode_key((1, 2)) == 'tuple:["int:1","int:2"]'

        # 解码: tuple:[...] → TupleCodec, else → scalar codec
        assert decode_key("int:42") == 42
        assert decode_key('tuple:["int:1","int:2"]') == (1, 2)

    def test_set_binary_operations(self, domain, clean_redis):
        ns = domain.get_namespace("spec_verification")

        ns["sa"] = {1, 2, 3}
        ns["sb"] = {2, 3, 4}

        sa = ns["sa"]
        sb = ns["sb"]

        union_result = sa | sb
        assert isinstance(union_result, set)
        assert union_result == {1, 2, 3, 4}

        inter_result = sa & sb
        assert isinstance(inter_result, set)
        assert inter_result == {2, 3}

        diff_result = sa - sb
        assert isinstance(diff_result, set)
        assert diff_result == {1}

        symdiff_result = sa ^ sb
        assert isinstance(symdiff_result, set)
        assert symdiff_result == {1, 4}

        sa | {5, 6}
        sa & {1, 5}
        sa - {1}
        sa ^ {1, 5}

    def test_set_inplace_operations(self, domain, clean_redis):
        # ── §4.2 集合运算 — 原地运算 ─────────────────────────────────
        ns = domain.get_namespace("spec_verification")

        ns["is"] = {1, 2, 3}
        iset = ns["is"]

        iset |= {4}
        assert set(iset) == {1, 2, 3, 4}

        iset &= {2, 3, 4}
        assert set(iset) == {2, 3, 4}

        iset -= {4}
        assert set(iset) == {2, 3}

        iset ^= {3, 5}
        assert set(iset) == {2, 5}

    def test_set_write_updates_timestamp(self, domain, clean_redis):
        # ── §4.3 写操作统一后处理 — updated_at ───────────────────────
        import time

        ns = domain.get_namespace("spec_verification")
        d = "testdomain"
        n = "spec_verification"

        ns["touch_s"] = {1}
        obj_touch = f"gpdc:{d}:{n}:obj:touch_s"
        before = clean_redis.hget(obj_touch, "updated_at")
        time.sleep(0.01)
        ns["touch_s"].add(2)
        after = clean_redis.hget(obj_touch, "updated_at")
        assert after >= before

    def test_set_reassign_ref_cleanup(self, domain, clean_redis):
        ns = domain.get_namespace("spec_verification")
        d = "testdomain"
        n = "spec_verification"

        ns["rx"] = {1, 2, 3}
        ns["ry"] = ns["rx"]
        ref_rx = f"gpdc:{d}:{n}:ref:rx"
        assert "ry" in clean_redis.smembers(ref_rx)

        ns["ry"] = {4, 5}
        assert "ry" not in clean_redis.smembers(ref_rx)
        assert set(ns["rx"]) == {1, 2, 3}
        assert set(ns["ry"]) == {4, 5}

    def test_set_cross_type_ref_sharing(self, domain, clean_redis):
        # ── §5.3 跨容器类型的引用 — Set 引用共享 ─────────────────────
        ns = domain.get_namespace("spec_verification")
        d = "testdomain"
        n = "spec_verification"

        ns["shared_s"] = {10, 20}
        ns["ref_s"] = ns["shared_s"]
        ref_shared_s = f"gpdc:{d}:{n}:ref:shared_s"
        assert "ref_s" in clean_redis.smembers(ref_shared_s)

        ref_s = ns["ref_s"]
        ref_s.add(30)
        assert 30 in ns["shared_s"]

    def test_set_delete_ref_cleanup(self, domain, clean_redis):
        ns = domain.get_namespace("spec_verification")
        d = "testdomain"
        n = "spec_verification"

        ns["del_x"] = {1, 2}
        ns["del_y"] = ns["del_x"]
        ref_del_x = f"gpdc:{d}:{n}:ref:del_x"
        assert "del_y" in clean_redis.smembers(ref_del_x)

        del ns["del_y"]
        assert "del_y" not in clean_redis.smembers(ref_del_x)
        assert set(ns["del_x"]) == {1, 2}

        ns["del_o"] = {5, 6}
        ns["del_r"] = ns["del_o"]
        with pytest.raises(CanonicalObjectReferencedError):
            del ns["del_o"]

        ns["alone_s"] = {7, 8}
        del ns["alone_s"]
        assert not clean_redis.exists(f"gpdc:{d}:{n}:obj:alone_s")
        assert not clean_redis.exists(f"gpdc:{d}:{n}:raw:alone_s")
        assert not clean_redis.exists(f"gpdc:{d}:{n}:ref:alone_s")

    def test_set_as_dict_value(self, domain, clean_redis):
        # ── §7.1 三种容器类型可以任意嵌套 — Set 作为 Dict 的值 ────
        from gpdatacached import SetObject, ListObject, DictObject

        ns = domain.get_namespace("spec_verification")
        d = "testdomain"
        n = "spec_verification"

        ns["complex"] = {
            "list": [1, 2],
            "dict": {"x": 10},
            "set_ref": {1, 2, 3},
        }
        cx_raw = f"gpdc:{d}:{n}:raw:complex"
        assert clean_redis.hget(cx_raw, "str:list").startswith("ref:gpdc:")
        assert clean_redis.hget(cx_raw, "str:dict").startswith("ref:gpdc:")
        assert clean_redis.hget(cx_raw, "str:set_ref").startswith("ref:gpdc:")

        cx = ns["complex"]
        assert isinstance(cx["list"], ListObject)
        assert isinstance(cx["dict"], DictObject)
        assert isinstance(cx["set_ref"], SetObject)
        assert list(cx["list"]) == [1, 2]
        assert cx["dict"]["x"] == 10
        assert set(cx["set_ref"]) == {1, 2, 3}

        # 匿名 set 的 ref set 包含父对象名称
        set_ref_val = clean_redis.hget(cx_raw, "str:set_ref")
        anon_set_name = set_ref_val.split(":obj:")[1]
        anon_set_ref_key = f"gpdc:{d}:{n}:ref:{anon_set_name}"
        assert "complex" in clean_redis.smembers(anon_set_ref_key)

    def test_set_as_list_element(self, domain, clean_redis):
        # ── §7.1 Set 作为 List 的元素 ────────────────────────────────
        from gpdatacached import SetObject

        ns = domain.get_namespace("spec_verification")
        d = "testdomain"
        n = "spec_verification"

        ns["list_with_set"] = [1, {2, 3}, "hello"]
        lws_raw = f"gpdc:{d}:{n}:raw:list_with_set"
        lws_items = clean_redis.lrange(lws_raw, 0, -1)
        assert lws_items[1].startswith("ref:gpdc:")

        lws = ns["list_with_set"]
        assert isinstance(lws[1], SetObject)
        assert set(lws[1]) == {2, 3}
        assert lws[2] == "hello"

    def test_set_copy_deepcopy(self, domain, clean_redis):
        # ── §7.2 赋值语义一致性 — Set 引用共享 / copy / deepcopy ──
        ns = domain.get_namespace("spec_verification")
        d = "testdomain"
        n = "spec_verification"

        # 引用共享
        ns["copy_src"] = {1, 2, 3}
        ns["copy_ref"] = ns["copy_src"]
        ref_copy_src = f"gpdc:{d}:{n}:ref:copy_src"
        assert "copy_ref" in clean_redis.smembers(ref_copy_src)
        ns["copy_ref"].add(4)
        assert 4 in ns["copy_src"]

        # copy
        ns["copy_shallow"] = ns["copy_src"].copy()
        ref_copy_src2 = f"gpdc:{d}:{n}:ref:copy_src"
        assert "copy_shallow" not in clean_redis.smembers(ref_copy_src2)

        # deepcopy
        ns["copy_deep"] = ns["copy_src"].deepcopy()
        assert set(ns["copy_deep"]) == {1, 2, 3, 4}

    def test_set_data_layout(self, domain, clean_redis):
        # ── §7.4 完整的数据布局对照 ──────────────────────────────────
        ns = domain.get_namespace("spec_verification")
        d = "testdomain"
        n = "spec_verification"

        ns["layout_set"] = {100}
        lo_obj = f"gpdc:{d}:{n}:obj:layout_set"
        lo_raw = f"gpdc:{d}:{n}:raw:layout_set"
        assert clean_redis.exists(lo_obj)
        assert clean_redis.exists(lo_raw)
        assert "int:100" in clean_redis.smembers(lo_raw)

    def test_set_gc_manual(self, domain, clean_redis):
        # ── §6 GC — 手动触发 ────────────────────────────────────────
        ns = domain.get_namespace("spec_verification")
        d = "testdomain"
        n = "spec_verification"

        ns["gc_target"] = {1, 2}
        gc_raw = f"gpdc:{d}:{n}:raw:gc_target"
        clean_redis.delete(f"gpdc:{d}:{n}:obj:gc_target")
        clean_redis.delete(f"gpdc:{d}:{n}:ref:gc_target")

        assert clean_redis.exists(gc_raw)
        domain.run_gc(namespace="spec_verification")
        assert not clean_redis.exists(gc_raw)

        # GC 不删除有效的命名对象
        ns["gc_valid"] = {5, 6}
        domain.run_gc(namespace="spec_verification")
        assert clean_redis.exists(f"gpdc:{d}:{n}:obj:gc_valid")
        assert clean_redis.exists(f"gpdc:{d}:{n}:raw:gc_valid")

        # GC 不删除有引用者的 raw (alias still exists pointing to owner)
        ns["gc_owned"] = {7, 8}
        ns["gc_ref"] = ns["gc_owned"]
        gc_owned_raw = f"gpdc:{d}:{n}:raw:gc_owned"
        domain.run_gc(namespace="spec_verification")
        assert clean_redis.exists(gc_owned_raw)

    def test_set_gc_disabled(self, domain, clean_redis):
        # ── §6.5 GC 触发方式 — gc_enabled=False 不启动线程 ──────────
        from gpdatacached.config import GPDCDomainConfig
        from gpdatacached.domain import GPDCDomain

        cfg_no_gc = GPDCDomainConfig(
            redis_host="127.0.0.1",
            redis_port=6379,
            redis_db=1,
            redis_password="123456",
            gc_enabled=False,
        )
        no_gc_domain = GPDCDomain("gc_disabled_spec", cfg_no_gc)
        assert no_gc_domain._gc_thread is None

    def test_set_domain_gc_method(self, domain, clean_redis):
        # ── §6.5 domain.run_gc() 方法存在 ────────────────────────────
        assert hasattr(domain, "run_gc")
        assert callable(domain.run_gc)

    def test_set_domain_close_method(self, domain, clean_redis):
        # ── §6.5 domain.close() 方法存在且可调用 ─────────────────────
        assert hasattr(domain, "close")
        assert callable(domain.close)

    def test_set_full_data_flow(self, domain, clean_redis):
        ns = domain.get_namespace("spec_verification")

        ns["primes"] = {2, 3, 5, 7, 11}
        ns["evens"] = {2, 4, 6, 8, 10}
        primes = ns["primes"]
        evens = ns["evens"]
        intersection = primes & evens
        assert isinstance(intersection, set)
        assert intersection == {2}

        ns["mixed"] = {"list": [1, 2], "nums": {1, 2, 3}}
        ns["ref"] = ns["mixed"]
        ref_d = ns["ref"]
        assert isinstance(ref_d["list"], ListObject)
        assert list(ref_d["list"]) == [1, 2]
        from gpdatacached import SetObject

        assert isinstance(ref_d["nums"], SetObject)
        assert set(ref_d["nums"]) == {1, 2, 3}
        del ns["ref"]
        del ns["mixed"]
