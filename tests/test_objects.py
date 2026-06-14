import datetime

import pytest

from gpdatacached.exceptions import GPDCError, LifecyclePermissionError, ObjectGoneError
from gpdatacached.keys import make_object_key, make_raw_key, make_ref_key
from gpdatacached.meta import ObjectMeta
from gpdatacached.objects import GPDCDataObject
from gpdatacached.ttl import TTLPolicy


class TestModuleExports:
    def test_base_module_exports_gpdcdataobject(self):
        assert GPDCDataObject is not None
        assert hasattr(GPDCDataObject, "name")
        assert hasattr(GPDCDataObject, "namespace")
        assert hasattr(GPDCDataObject, "domain")
        assert hasattr(GPDCDataObject, "uri")
        assert hasattr(GPDCDataObject, "type")
        assert hasattr(GPDCDataObject, "value")

    def test_package_reexports(self):
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
    def test_owner_handle_defaults(self, ns):
        ns["mylist"] = [1, 2, 3]
        obj = ns.get_object("mylist")
        assert obj.name == "mylist"
        assert obj.canonical_name == "mylist"
        assert obj.is_reference is False
        assert obj.uri == obj.canonical_uri

    def test_alias_handle_identity(self, ns):
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
        ns["x"] = 42
        obj = ns.get_object("x")
        assert obj.is_reference is False
        assert obj.canonical_name == "x"


class TestEqIdentity:
    def test_same_key_equal(self, ns):
        ns["x"] = [1, 2, 3]
        a = ns.get_object("x")
        b = ns.get_object("x")
        assert a == b

    def test_different_key_not_equal(self, ns):
        ns["a"] = [1, 2]
        ns["b"] = [1, 2]
        assert ns.get_object("a") != ns.get_object("b")

    def test_scalar_same_key_equal(self, ns):
        ns["n"] = 42
        assert ns.get_object("n") == ns.get_object("n")

    def test_scalar_different_key_not_equal(self, ns):
        ns["x"] = 42
        ns["y"] = 42
        assert ns.get_object("x") != ns.get_object("y")

    def test_container_vs_python_returns_not_implemented(self, ns):
        ns["lst"] = [1, 2, 3]
        obj = ns.get_object("lst")
        assert obj != [1, 2, 3]

    def test_dict_object_vs_python_dict(self, ns):
        ns["d"] = {"a": 1}
        obj = ns.get_object("d")
        assert obj != {"a": 1}

    def test_set_object_vs_python_set(self, ns):
        ns["s"] = {1, 2}
        obj = ns.get_object("s")
        assert obj != {1, 2}

    def test_scalar_vs_python_value(self, ns):
        ns["x"] = 42
        obj = ns.get_object("x")
        assert obj != 42

    def test_alias_equal_to_canonical(self, ns):
        ns["owner"] = [10, 20]
        owner = ns.get_object("owner")
        ns["alias"] = owner
        assert ns.get_object("alias") == ns.get_object("owner")


class TestUnhashable:
    @pytest.mark.parametrize("value", [[1, 2, 3], {"a": 1}, {1, 2}, 42])
    def test_object_unhashable(self, ns, value):
        ns["x"] = value
        obj = ns.get_object("x")
        with pytest.raises(TypeError):
            hash(obj)


class TestObjectsLifecycle:
    def _setup_parent_and_child(self, r, domain, ns):
        parent_name = "parent"
        child_name = "~anon:child1"

        parent_key = make_object_key(domain, ns, parent_name)
        parent_raw = make_raw_key(domain, ns, parent_name)
        ObjectMeta(
            type="collection:list",
            value=parent_raw,
            cache_mode="permanent",
            ttl=0,
            owner=parent_name,
        ).save(r, parent_key)
        r.rpush(parent_raw, f"ref:{make_object_key(domain, ns, child_name)}")

        child_key = make_object_key(domain, ns, child_name)
        child_raw = make_raw_key(domain, ns, child_name)
        ObjectMeta(
            type="collection:dict",
            value=child_raw,
            cache_mode="permanent",
            ttl=0,
            owner=parent_name,
        ).save(r, child_key)
        r.hset(make_ref_key(domain, ns, child_name), parent_name, "1")

        return parent_name, child_name

    def test_set_lifecycle_updates_meta_and_redis(self, domain, ns):
        ns["obj"] = 42
        obj = ns.get_object("obj")
        policy = TTLPolicy(cache_mode="ttl", declared_ttl=60, expire_seconds=60)
        obj.set_lifecycle(policy)
        meta = obj.meta
        assert meta.cache_mode == "ttl"
        assert meta.ttl == 60
        assert domain.redis.ttl(obj._canonical_redis_key()) > 0

    def test_set_lifecycle_propagates_to_owned_children(self, domain, ns, clean_redis):
        r = clean_redis
        d = domain.name
        n = ns.name

        parent_name, child_name = self._setup_parent_and_child(r, d, n)

        parent_obj = ns.get_object(parent_name)
        policy = TTLPolicy(cache_mode="ttl", declared_ttl=120, expire_seconds=120)
        parent_obj.set_lifecycle(policy)

        child_key = make_object_key(d, n, child_name)
        child_meta = ObjectMeta.load(r, child_key)
        assert child_meta.cache_mode == "ttl"
        assert child_meta.ttl == 120

    def test_take_ownership_requires_reference(self, domain, ns):
        ns["obj"] = 42
        obj = ns.get_object("obj")
        with pytest.raises(TypeError, match="named reference"):
            obj.take_ownership()

    def test_take_ownership_transfers_owner(self, domain, ns, clean_redis):
        r = clean_redis
        d = domain.name
        n = ns.name

        parent_name, child_name = self._setup_parent_and_child(r, d, n)

        ref_name = "myref"
        ref_alias_key = make_object_key(d, n, ref_name)
        child_key = make_object_key(d, n, child_name)
        r.set(ref_alias_key, f"ref:{child_key}")
        r.hset(make_ref_key(d, n, child_name), ref_name, "1")

        ref_obj = ns.get_object(ref_name)
        assert ref_obj.is_reference
        ref_obj.take_ownership()

        updated_meta = ObjectMeta.load(r, child_key)
        assert updated_meta.owner == ref_obj.canonical_name

    def test_cache_mode_setter_checks_owner(self, domain, ns, clean_redis):
        r = clean_redis
        d = domain.name
        n = ns.name

        parent_name, child_name = self._setup_parent_and_child(r, d, n)

        parent_obj = ns.get_object(parent_name)
        child_obj = parent_obj[0]

        with pytest.raises(LifecyclePermissionError):
            child_obj.update_cache_mode_and_ttl("ttl", 60)

    def test_persist_checks_owner(self, domain, ns, clean_redis):
        r = clean_redis
        d = domain.name
        n = ns.name

        parent_name, child_name = self._setup_parent_and_child(r, d, n)

        parent_obj = ns.get_object(parent_name)
        child_obj = parent_obj[0]

        with pytest.raises(LifecyclePermissionError):
            child_obj.persist()


class TestScalarValueGone:
    def test_int_value_raises_object_gone_error(self, ns, raw_redis):
        ns["x"] = 42
        obj = ns.get_object("x")
        raw_redis.delete(obj._canonical_redis_key())
        with pytest.raises(ObjectGoneError):
            _ = obj.value

    def test_str_value_raises_object_gone_error(self, ns, raw_redis):
        ns["s"] = "hello"
        obj = ns.get_object("s")
        raw_redis.delete(obj._canonical_redis_key())
        with pytest.raises(ObjectGoneError):
            _ = obj.value

    def test_corrupted_int_raises_gpdc_error(self, ns, raw_redis):
        ns["x"] = 42
        obj = ns.get_object("x")
        raw_redis.hset(obj._canonical_redis_key(), "value", "")
        with pytest.raises(GPDCError, match="Corrupted"):
            _ = obj.value

    def test_corrupted_str_raises_gpdc_error(self, ns, raw_redis):
        ns["s"] = "hello"
        obj = ns.get_object("s")
        raw_redis.hset(obj._canonical_redis_key(), "value", "")
        with pytest.raises(GPDCError, match="Corrupted"):
            _ = obj.value

    def test_none_object_returns_none(self, ns):
        ns["n"] = None
        obj = ns.get_object("n")
        assert obj.value is None

    def test_none_object_value_roundtrip(self, ns):
        ns["n"] = None
        obj = ns.get_object("n")
        obj.value = None
        assert obj.value is None


class TestObjectGoneErrorScalar:
    def _delete_meta(self, obj, raw_redis):
        raw_redis.delete(obj._canonical_redis_key())

    def test_update_cache_mode_and_ttl_raises_on_gone(self, ns, raw_redis):
        ns["x"] = 42
        obj = ns.get_object("x")
        self._delete_meta(obj, raw_redis)
        with pytest.raises(ObjectGoneError, match="no longer exists"):
            obj.update_cache_mode_and_ttl("ttl", 60)

    def test_expire_in_raises(self, ns, raw_redis):
        ns["x"] = 42
        obj = ns.get_object("x")
        self._delete_meta(obj, raw_redis)
        with pytest.raises(ObjectGoneError, match="no longer exists"):
            obj.expire_in(60)

    def test_expire_at_raises(self, ns, raw_redis):
        ns["x"] = 42
        obj = ns.get_object("x")
        self._delete_meta(obj, raw_redis)
        future = datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1)
        with pytest.raises(ObjectGoneError, match="no longer exists"):
            obj.expire_at(future)

    def test_persist_raises(self, ns, raw_redis):
        ns["x"] = 42
        obj = ns.get_object("x")
        self._delete_meta(obj, raw_redis)
        with pytest.raises(ObjectGoneError, match="no longer exists"):
            obj.persist()

    def test_scalar_value_setter_raises(self, ns, raw_redis):
        ns["x"] = 42
        obj = ns.get_object("x")
        self._delete_meta(obj, raw_redis)
        with pytest.raises(ObjectGoneError, match="no longer exists"):
            obj.value = 99


class TestObjectGoneErrorContainer:
    def _delete_meta(self, obj, raw_redis):
        raw_redis.delete(obj._canonical_redis_key())

    def test_list_append_raises(self, ns, raw_redis):
        ns["lst"] = [1, 2]
        obj = ns.get_object("lst")
        self._delete_meta(obj, raw_redis)
        with pytest.raises(ObjectGoneError, match="no longer exists"):
            obj.append(3)

    def test_set_add_raises(self, ns, raw_redis):
        ns["s"] = {1, 2}
        obj = ns.get_object("s")
        self._delete_meta(obj, raw_redis)
        with pytest.raises(ObjectGoneError, match="no longer exists"):
            obj.add(3)

    def test_dict_setitem_raises(self, ns, raw_redis):
        ns["d"] = {"a": 1}
        obj = ns.get_object("d")
        self._delete_meta(obj, raw_redis)
        with pytest.raises(ObjectGoneError, match="no longer exists"):
            obj["b"] = 2


class TestObjectGoneErrorRead:
    def _delete_meta(self, obj, raw_redis):
        raw_redis.delete(obj._canonical_redis_key())

    def test_cache_mode_raises_on_gone(self, ns, raw_redis):
        ns["x"] = 42
        obj = ns.get_object("x")
        self._delete_meta(obj, raw_redis)
        with pytest.raises(ObjectGoneError, match="no longer exists"):
            _ = obj.cache_mode

    def test_ttl_raises_on_gone(self, ns, raw_redis):
        ns["x"] = 42
        obj = ns.get_object("x")
        self._delete_meta(obj, raw_redis)
        with pytest.raises(ObjectGoneError, match="no longer exists"):
            _ = obj.ttl

    def test_created_at_raises_on_gone(self, ns, raw_redis):
        ns["x"] = 42
        obj = ns.get_object("x")
        self._delete_meta(obj, raw_redis)
        with pytest.raises(ObjectGoneError, match="no longer exists"):
            _ = obj.created_at

    def test_updated_at_raises_on_gone(self, ns, raw_redis):
        ns["x"] = 42
        obj = ns.get_object("x")
        self._delete_meta(obj, raw_redis)
        with pytest.raises(ObjectGoneError, match="no longer exists"):
            _ = obj.updated_at


class TestScalarTypeEnforcement:
    def test_scalar_object_rejects_value_of_different_declared_type(self, ns):
        ns["x"] = 1
        obj = ns.get_object("x")
        assert obj.value == 1

        with pytest.raises(TypeError):
            obj.value = "abc"

        assert obj.value == 1


class TestObjectGoneErrorAttributes:
    def test_exception_carries_uri(self, ns, raw_redis):
        ns["x"] = 42
        obj = ns.get_object("x")
        raw_redis.delete(obj._canonical_redis_key())
        with pytest.raises(ObjectGoneError) as exc_info:
            obj.update_cache_mode_and_ttl("ttl", 60)
        assert exc_info.value.object_uri == obj.canonical_uri
        assert "gpdc://" in str(exc_info.value)
