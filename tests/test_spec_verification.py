import datetime

import pytest

from gpdatacached import GPDCNamespace
from gpdatacached.keys import GLOBAL_META_KEY


class TestSpecVerification:
    """Spec verification tests"""

    def test_domain_ops(self, domain, clean_redis):
        ns = domain.get_namespace("spec_verification")
        test_ns = domain.get_namespace("test_ns")

        assert clean_redis.hget(GLOBAL_META_KEY, "version") == "0.1.0"
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
        obj = test_ns.get_object("test_get", 100)
        assert obj.value == 100
        assert test_ns["test_get"] == 100

    def test_list_ops(self, domain, clean_redis):
        ns = domain.get_namespace("spec_verification")
        d = "testdomain"
        n = "spec_verification"

        # ── §9.3 创建 List ──────────────────────────────────────────────
        ns["mylist"] = [1, 2, 3]

        obj_key = f"gpdc:{d}:{n}:obj:mylist"
        raw_key = f"gpdc:{d}:{n}:raw:mylist"
        ref_key = f"gpdc:{d}:{n}:ref:mylist"

        assert clean_redis.hget(obj_key, "type") == "collection:list"
        assert clean_redis.hget(obj_key, "codec") == "native"
        assert clean_redis.hget(obj_key, "value") == raw_key
        assert clean_redis.hget(obj_key, "cache_mode") == "permanent"
        assert clean_redis.hget(obj_key, "ttl") == ""
        assert clean_redis.hget(obj_key, "created_at") is not None
        assert clean_redis.hget(obj_key, "updated_at") is not None

        assert clean_redis.exists(raw_key)
        assert clean_redis.lrange(raw_key, 0, -1) == ["int:1", "int:2", "int:3"]

        assert clean_redis.smembers(ref_key) == set()

        # ── §2.1 类型标识 ───────────────────────────────────────────────
        from gpdatacached.objects import ListObject

        lst = ns["mylist"]
        assert isinstance(lst, ListObject)
        assert lst.type == "collection:list"

        # ── §8 ContainerObject 属性 ─────────────────────────────────────
        assert lst.raw_key == raw_key
        assert lst.ref_key == ref_key
        assert lst.referrers() == set()

        # ── §9.1 MutableSequence 方法 ───────────────────────────────────
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
        assert list(lst) == [1, 2, 3, 4]

        lst.extend([5, 6])
        assert list(lst) == [1, 2, 3, 4, 5, 6]

        lst[0] = 10
        assert lst[0] == 10

        lst.pop()
        assert list(lst) == [10, 2, 3, 4, 5]

        lst.pop(0)
        assert list(lst) == [2, 3, 4, 5]

        lst.insert(0, 1)
        assert list(lst) == [1, 2, 3, 4, 5]

        lst.remove(3)
        assert list(lst) == [1, 2, 4, 5]

        del lst[0]
        assert list(lst) == [2, 4, 5]

        lst.reverse()
        assert list(lst) == [5, 4, 2]

        lst.sort()
        assert list(lst) == [2, 4, 5]

        lst.clear()
        assert len(lst) == 0
        assert list(lst) == []

        # ── §6.2 容器赋值 — 引用共享 ────────────────────────────────────
        ns["a"] = [1, 2, 3]
        ns["b"] = ns["a"]

        obj_a = f"gpdc:{d}:{n}:obj:a"
        obj_b = f"gpdc:{d}:{n}:obj:b"
        raw_a = f"gpdc:{d}:{n}:raw:a"
        raw_b = f"gpdc:{d}:{n}:raw:b"
        ref_a = f"gpdc:{d}:{n}:ref:a"

        assert clean_redis.hget(obj_b, "value") == clean_redis.hget(obj_a, "value")
        assert clean_redis.hget(obj_b, "value") == raw_a
        assert not clean_redis.exists(raw_b)
        assert "b" in clean_redis.smembers(ref_a)
        assert "b" in ns["a"].referrers()

        ns["b"].append(4)
        assert list(ns["a"]) == [1, 2, 3, 4]

        # ── §6.1 标量赋值 — 值复制 ─────────────────────────────────────
        ns["s1"] = 10
        ns["s2"] = ns["s1"]
        assert ns["s1"] == 10
        assert ns["s2"] == 10
        ns["s2"] = 20
        assert ns["s1"] == 10

        # ── §6.3 重新赋值的引用维护 ──────────────────────────────────────
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

        # ── §7 copy / deepcopy ──────────────────────────────────────────
        ns["src"] = [1, 2, 3]
        ns["c1"] = ns["src"].copy()
        ns["c2"] = ns["src"].deepcopy()

        assert list(ns["c1"]) == [1, 2, 3]
        assert list(ns["c2"]) == [1, 2, 3]

        ns["c1"].append(4)
        assert list(ns["src"]) == [1, 2, 3]
        assert list(ns["c1"]) == [1, 2, 3, 4]

        ns["c2"].append(5)
        assert list(ns["src"]) == [1, 2, 3]
        assert list(ns["c2"]) == [1, 2, 3, 5]

        ref_src = f"gpdc:{d}:{n}:ref:src"
        assert "c1" not in clean_redis.smembers(ref_src)
        assert "c2" not in clean_redis.smembers(ref_src)

        raw_c1 = f"gpdc:{d}:{n}:raw:c1"
        raw_c2 = f"gpdc:{d}:{n}:raw:c2"
        obj_c1 = f"gpdc:{d}:{n}:obj:c1"
        obj_c2 = f"gpdc:{d}:{n}:obj:c2"
        assert clean_redis.hget(obj_c1, "value") == raw_c1
        assert clean_redis.hget(obj_c2, "value") == raw_c2

        # ── §10.1 删除无引用的容器对象 ──────────────────────────────────
        ns["alone"] = [1, 2]
        del ns["alone"]
        assert not clean_redis.exists(f"gpdc:{d}:{n}:obj:alone")
        assert not clean_redis.exists(f"gpdc:{d}:{n}:raw:alone")
        assert not clean_redis.exists(f"gpdc:{d}:{n}:ref:alone")

        # ── §10.1 删除有引用的容器对象（保留 raw 和 ref） ──────────────
        ns["owner"] = [10, 20]
        ns["sharer"] = ns["owner"]
        del ns["owner"]
        assert not clean_redis.exists(f"gpdc:{d}:{n}:obj:owner")
        assert clean_redis.exists(f"gpdc:{d}:{n}:raw:owner")
        assert clean_redis.exists(f"gpdc:{d}:{n}:ref:owner")
        assert list(ns["sharer"]) == [10, 20]

        # ── §10.1 删除引用者（从原 ref set 移除） ───────────────────────
        ns["p"] = [1, 2, 3]
        ns["q"] = ns["p"]
        ref_p = f"gpdc:{d}:{n}:ref:p"
        assert "q" in clean_redis.smembers(ref_p)
        del ns["q"]
        assert "q" not in clean_redis.smembers(ref_p)
        assert list(ns["p"]) == [1, 2, 3]

        # ── §9.2 写操作更新 updated_at ──────────────────────────────────
        import time

        ns["touch_test"] = [1]
        obj_touch = f"gpdc:{d}:{n}:obj:touch_test"
        before = clean_redis.hget(obj_touch, "updated_at")
        time.sleep(0.01)
        ns["touch_test"].append(2)
        after = clean_redis.hget(obj_touch, "updated_at")
        assert after >= before

        # ── §3 标量元素编码（多种类型） ──────────────────────────────────
        import datetime

        ns["mixed"] = [42, 1.5, True, "hello", None, datetime.date(2020, 1, 1)]
        mixed_lst = ns["mixed"]
        assert mixed_lst[0] == 42
        assert mixed_lst[1] == 1.5
        assert mixed_lst[2] is True
        assert mixed_lst[3] == "hello"
        assert mixed_lst[4] is None
        assert mixed_lst[5] == datetime.date(2020, 1, 1)

    def test_dict_ops(self, domain, clean_redis):
        ns = domain.get_namespace("spec_verification")
        d = "testdomain"
        n = "spec_verification"

        import time

        from gpdatacached.objects import DictObject, ListObject
        from gpdatacached.codec import GPDCScalarCodec

        # ── §2 DictObject 类型标识与元数据 ────────────────────────────
        ns["mydict"] = {"name": "test"}

        obj_key = f"gpdc:{d}:{n}:obj:mydict"
        raw_key = f"gpdc:{d}:{n}:raw:mydict"
        ref_key = f"gpdc:{d}:{n}:ref:mydict"

        assert clean_redis.hget(obj_key, "type") == "collection:dict"
        assert clean_redis.hget(obj_key, "codec") == "native"
        assert clean_redis.hget(obj_key, "value") == raw_key
        assert clean_redis.hget(obj_key, "cache_mode") == "permanent"
        assert clean_redis.hget(obj_key, "ttl") == ""
        assert clean_redis.hget(obj_key, "created_at") is not None
        assert clean_redis.hget(obj_key, "updated_at") is not None

        # ── §2.3 Raw 数据格式 ────────────────────────────────────────
        assert clean_redis.hget(raw_key, "str:name") == "str:test"
        assert clean_redis.smembers(ref_key) == set()

        # ── §2.1 类型标识 ────────────────────────────────────────────
        dct = ns["mydict"]
        assert isinstance(dct, DictObject)
        assert dct.type == "collection:dict"

        # ── §9 DictObject MutableMapping 方法 ─────────────────────────
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

        # ── §4 Tuple Key 编码规则 ─────────────────────────────────────
        # §4.2 编码示例
        assert GPDCScalarCodec.encode_tuple(("a", "b")) == 'tuple:["str:a","str:b"]'
        assert GPDCScalarCodec.encode_tuple(("a,b", "c")) == 'tuple:["str:a,b","str:c"]'
        assert GPDCScalarCodec.encode_tuple((1, "x", 2.5)) == 'tuple:["int:1","str:x","float:2.5"]'
        assert GPDCScalarCodec.encode_tuple((True, None)) == 'tuple:["bool:True","none:None"]'

        # §4.3 roundtrip
        assert GPDCScalarCodec.decode_tuple('tuple:["str:a","str:b"]') == ("a", "b")
        assert GPDCScalarCodec.decode_tuple('tuple:["int:1","str:x","float:2.5"]') == (1, "x", 2.5)

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

        # ── §3 容器元素编码规则 ────────────────────────────────────────
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

        # ── §5 匿名对象 ──────────────────────────────────────────────
        # §5.2 匿名对象命名 ~anon:<uuid>
        anon_ref = clean_redis.hget(nd_raw, "str:a")
        assert ":obj:~anon:" in anon_ref
        anon_name = anon_ref.split(":obj:")[1]
        assert anon_name.startswith("~anon:")

        # §5.3 匿名对象的元数据
        anon_obj_key = f"gpdc:{d}:{n}:obj:{anon_name}"
        assert clean_redis.hget(anon_obj_key, "type") == "collection:list"
        assert clean_redis.hget(anon_obj_key, "codec") == "native"
        assert clean_redis.hget(anon_obj_key, "cache_mode") == "permanent"
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
        assert clean_redis.hget(anon_obj_key, "cache_mode") == clean_redis.hget(
            f"gpdc:{d}:{n}:obj:nest_dict", "cache_mode"
        )

        # §5.5 匿名对象不出现在 namespace 迭代中
        for name in ns:
            assert not name.startswith("~")

        # §5.5 匿名对象不能通过 ns["~anon:xxx"] 直接访问
        with pytest.raises(ValueError):
            ns[anon_name]

        # ── §6 容器嵌套完整流程 ──────────────────────────────────────
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

        # ── §9.3 value 编解码（运行时嵌套） ───────────────────────────
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

        # ── §8 copy() 与 deepcopy()（嵌套场景） ───────────────────────
        ns["original"] = {"a": [1, 2, 3], "b": "hello"}

        # §8.1 copy() — 浅拷贝
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

        # ── §10 删除嵌套容器 ─────────────────────────────────────────
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

        # §10.2 级联删除示例 — 有引用者时保留匿名对象
        ns["x"] = {"a": [1, 2, 3]}
        ns["y"] = ns["x"]
        x_raw = f"gpdc:{d}:{n}:raw:x"
        x_anon_ref = clean_redis.hget(x_raw, "str:a")
        x_anon_name = x_anon_ref.split(":obj:")[1]

        del ns["x"]
        assert not clean_redis.exists(f"gpdc:{d}:{n}:obj:x")
        assert clean_redis.exists(f"gpdc:{d}:{n}:obj:{x_anon_name}")
        assert clean_redis.exists(f"gpdc:{d}:{n}:raw:x")

        # §10.1 Dict 引用共享
        ns["d_ref_a"] = {"x": 1}
        ns["d_ref_b"] = ns["d_ref_a"]
        ref_a = f"gpdc:{d}:{n}:ref:d_ref_a"
        assert "d_ref_b" in clean_redis.smembers(ref_a)
        ns["d_ref_b"]["y"] = 2
        assert ns["d_ref_a"]["y"] == 2

        # 删除引用共享的容器
        del ns["d_ref_a"]
        assert not clean_redis.exists(f"gpdc:{d}:{n}:obj:d_ref_a")
        assert clean_redis.exists(f"gpdc:{d}:{n}:raw:d_ref_a")

        # ── §9.4 写操作更新 updated_at ───────────────────────────────
        ns["touch_d"] = {"a": 1}
        obj_touch = f"gpdc:{d}:{n}:obj:touch_d"
        before = clean_redis.hget(obj_touch, "updated_at")
        time.sleep(0.01)
        ns["touch_d"]["b"] = 2
        after = clean_redis.hget(obj_touch, "updated_at")
        assert after >= before

        # ── §11 完整数据流示例 ───────────────────────────────────────
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
