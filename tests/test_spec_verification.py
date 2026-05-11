import datetime

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
