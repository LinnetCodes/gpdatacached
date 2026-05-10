# GPDCNamespace Value-Semantics Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor GPDCNamespace so `__getitem__` returns Python native values, and add `get_object()` for GPDCScalarObject access.

**Architecture:** Extract current `__getitem__` logic into private `_get_object()` helper. New `__getitem__` calls `_get_object().value`. New public `get_object(key, default=SENTINEL)` calls `_get_object()` with setdefault-like semantics. No override needed for `setdefault()` — inherited MutableMapping version works correctly.

**Tech Stack:** Python, Redis, pytest

---

### Task 1: Refactor `__getitem__` and add `_get_object` / `get_object`

**Files:**
- Modify: `src/gpdatacached/namespace.py:150-159`

- [ ] **Step 1: Add `_MISSING` sentinel, refactor `__getitem__` into `_get_object`, update `__getitem__`, add `get_object`**

Replace lines 150-159 in `src/gpdatacached/namespace.py` with:

```python
_MISSING = object()


class GPDCNamespace(MutableMapping):
    # ... (existing class body unchanged until __getitem__) ...

    def _get_object(self, key: str) -> GPDCScalarObject:
        validate_object_name(key)
        obj_key = make_object_key(self._domain.name, self._name, key)
        type_str = self._redis().hget(obj_key, "type")
        if type_str is None:
            raise KeyError(f"{key!r} not found in namespace {self._name!r}")
        cls = SCALAR_TYPE_CLASS_MAP.get(type_str)
        if cls is None:
            raise ValueError(f"Unknown GPDC type: {type_str!r}")
        return cls(key, self)

    def __getitem__(self, key: str):
        return self._get_object(key).value

    def get_object(self, key: str, default=object()):
        try:
            return self._get_object(key)
        except KeyError:
            if isinstance(default, object) and default is _MISSING:
                raise
            self[key] = default
            return self._get_object(key)
```

Wait — the sentinel pattern with `object()` default arg won't work since it creates a new object each time. Use module-level `_MISSING` sentinel as default:

```python
_MISSING = object()

# inside class:
def get_object(self, key: str, default=_MISSING):
    try:
        return self._get_object(key)
    except KeyError:
        if default is _MISSING:
            raise
        self[key] = default
        return self._get_object(key)
```

- [ ] **Step 2: Run existing tests to see which ones break (expected)**

Run: `./venv/bin/python -m pytest tests/test_namespace.py tests/test_integration.py tests/test_spec_verification.py -v 2>&1 | head -100`
Expected: Multiple test failures in `TestNamespaceGetItem`, `TestObjectValueSetter`, `TestCacheModeAndTTL`, `TestNamespaceMutableMapping`, integration tests — any test that does `ns["x"].value` or `ns["x"].some_property` will now fail because `ns["x"]` returns a Python value, not an object.

- [ ] **Step 3: Commit the namespace.py refactor**

```bash
git add src/gpdatacached/namespace.py
git commit -m "refactor: __getitem__ returns native values, add _get_object and get_object"
```

---

### Task 2: Update `test_namespace.py`

**Files:**
- Modify: `tests/test_namespace.py`

- [ ] **Step 1: Update `TestNamespaceGetItem` tests**

The following tests need updating. `ns["key"]` now returns a Python value, not a GPDCScalarObject. Tests that check `isinstance(obj, IntObject)` or `obj.uri`/`obj.name` need to use `ns.get_object("key")` instead.

**test_get_int_returns_int_object** (line 114-120): Change to return native int:
```python
def test_get_int_returns_native_value(self, ns):
    ns["n"] = 42
    assert ns["n"] == 42
    assert isinstance(ns["n"], int)
```

**test_get_str_returns_str_object** (line 122-128):
```python
def test_get_str_returns_native_value(self, ns):
    ns["s"] = "hello"
    assert ns["s"] == "hello"
    assert isinstance(ns["s"], str)
```

**test_get_bool_returns_bool_object** (line 130-136):
```python
def test_get_bool_returns_native_value(self, ns):
    ns["b"] = True
    assert ns["b"] is True
    assert isinstance(ns["b"], bool)
```

**test_get_returns_correct_uri** (line 142-145): Move to use `get_object`:
```python
def test_get_object_returns_correct_uri(self, ns):
    ns["counter"] = 1
    obj = ns.get_object("counter")
    assert obj.uri == "gpdc://testdomain/testns/counter"
```

**test_get_returns_correct_name** (line 147-150):
```python
def test_get_object_returns_correct_name(self, ns):
    ns["counter"] = 1
    obj = ns.get_object("counter")
    assert obj.name == "counter"
```

**test_value_copy_semantics** (line 152-157): `ns["a"]` now returns int, not object. Copy semantics still hold:
```python
def test_value_copy_semantics(self, ns):
    ns["a"] = 10
    ns["b"] = ns["a"]
    assert ns["b"] == 10
    ns["b"] = 20
    assert ns["a"] == 10
```

- [ ] **Step 2: Update tests that do `ns["x"].value` or `ns["x"]` as object**

**test_set_float** (line 52-55): `ns["ratio"]` now returns float directly:
```python
def test_set_float(self, ns):
    ns["ratio"] = 3.14
    assert abs(ns["ratio"] - 3.14) < 1e-9
```

**test_set_datetime** (line 57-60):
```python
def test_set_datetime(self, ns):
    dt = datetime.datetime(2024, 6, 15, 9, 0, 0)
    ns["ts"] = dt
    assert ns["ts"] == dt
```

**test_set_date** (line 62-65):
```python
def test_set_date(self, ns):
    d = datetime.date(2024, 6, 15)
    ns["day"] = d
    assert ns["day"] == d
```

**test_set_time** (line 67-70):
```python
def test_set_time(self, ns):
    t = datetime.time(9, 30, 0)
    ns["t"] = t
    assert ns["t"] == t
```

**test_update_changes_value** (line 107-110):
```python
def test_update_changes_value(self, ns):
    ns["x"] = 10
    ns["x"] = 99
    assert ns["x"] == 99
```

- [ ] **Step 3: Update `TestObjectValueSetter` tests** (line 201-219)

These tests need `ns.get_object("x")` to get the object for `.value` setter:
```python
class TestObjectValueSetter:
    def test_set_value_via_obj(self, ns):
        ns["x"] = 10
        obj = ns.get_object("x")
        obj.value = 99
        assert ns["x"] == 99

    def test_set_value_updates_updated_at(self, ns, clean_redis):
        import time

        ns["x"] = 1
        key = make_object_key("testdomain", "testns", "x")
        first = clean_redis.hget(key, "updated_at")
        time.sleep(0.01)
        obj = ns.get_object("x")
        obj.value = 2
        second = clean_redis.hget(key, "updated_at")
        assert second >= first
```

- [ ] **Step 4: Update `TestCacheModeAndTTL` tests** (line 222-280)

Every `ns["x"]` that was used as an object needs `ns.get_object("x")`:
```python
class TestCacheModeAndTTL:
    def test_default_cache_mode_is_permanent(self, ns):
        ns["x"] = 1
        obj = ns.get_object("x")
        assert obj.cache_mode == "permanent"

    def test_set_cache_mode_ttl(self, ns):
        ns["x"] = 1
        obj = ns.get_object("x")
        obj.cache_mode = "ttl"
        assert ns.get_object("x").cache_mode == "ttl"

    def test_set_ttl_applies_expire(self, ns, clean_redis):
        ns["x"] = 1
        obj = ns.get_object("x")
        obj.cache_mode = "ttl"
        obj.ttl = 60
        key = make_object_key("testdomain", "testns", "x")
        remaining = clean_redis.ttl(key)
        assert 55 <= remaining <= 60

    def test_persist_removes_ttl(self, ns, clean_redis):
        ns["x"] = 1
        obj = ns.get_object("x")
        obj.cache_mode = "ttl"
        obj.ttl = 60
        obj.persist()
        key = make_object_key("testdomain", "testns", "x")
        assert clean_redis.ttl(key) == -1

    def test_remaining_ttl_permanent(self, ns):
        ns["x"] = 1
        obj = ns.get_object("x")
        assert obj.remaining_ttl() == -1

    def test_expire_in(self, ns, clean_redis):
        ns["x"] = 1
        obj = ns.get_object("x")
        obj.expire_in(100)
        key = make_object_key("testdomain", "testns", "x")
        remaining = clean_redis.ttl(key)
        assert 95 <= remaining <= 100

    def test_sliding_mode_resets_ttl_on_write(self, ns, clean_redis):
        ns["x"] = 1
        obj = ns.get_object("x")
        obj.cache_mode = "sliding"
        obj.ttl = 300
        obj.value = 2
        key = make_object_key("testdomain", "testns", "x")
        remaining = clean_redis.ttl(key)
        assert 295 <= remaining <= 300

    def test_invalid_cache_mode_raises(self, ns):
        ns["x"] = 1
        obj = ns.get_object("x")
        with pytest.raises(ValueError):
            obj.cache_mode = "invalid"
```

- [ ] **Step 5: Update `TestNamespaceMutableMapping` tests** (line 346-529)

**test_values_are_gpdc_objects** (line 426-433): `.values()` now returns native values:
```python
def test_values_are_native_values(self, ns):
    ns["x"] = 10
    vals = list(ns.values())
    assert len(vals) == 1
    assert vals[0] == 10
```

**test_items** (line 435-442): `.items()` values are now native:
```python
def test_items(self, ns):
    ns["a"] = 1
    ns["b"] = 2
    items = dict(ns.items())
    assert set(items.keys()) == {"a", "b"}
    assert items["a"] == 1
    assert items["b"] == 2
```

**test_get_existing** (line 444-450): `.get()` returns native value:
```python
def test_get_existing(self, ns):
    ns["x"] = 42
    result = ns.get("x")
    assert result == 42
```

**test_pop_existing** (line 459-465): `.pop()` returns native value:
```python
def test_pop_existing(self, ns):
    ns["x"] = 55
    result = ns.pop("x")
    assert result == 55
    assert "x" not in ns
```

**test_update_from_dict** (line 477-481):
```python
def test_update_from_dict(self, ns):
    ns.update({"a": 1, "b": 2})
    assert ns["a"] == 1
    assert ns["b"] == 2
    assert len(ns) == 2
```

**test_setdefault_missing** (line 518-524): Now returns native value in both branches:
```python
def test_setdefault_missing(self, ns):
    result = ns.setdefault("x", 42)
    assert result == 42
    assert "x" in ns
    assert ns["x"] == 42
```

**test_setdefault_existing** (line 526-529): Now returns native value (was GPDCScalarObject):
```python
def test_setdefault_existing(self, ns):
    ns["x"] = 10
    result = ns.setdefault("x", 99)
    assert result == 10
```

- [ ] **Step 6: Add new `TestGetObject` test class**

Add after `TestNamespaceGetItem`:
```python
class TestGetObject:
    def test_get_object_existing_returns_scalar_object(self, ns):
        from gpdatacached.objects import IntObject

        ns["x"] = 42
        obj = ns.get_object("x")
        assert isinstance(obj, IntObject)
        assert obj.value == 42

    def test_get_object_missing_raises(self, ns):
        with pytest.raises(KeyError):
            ns.get_object("nonexistent")

    def test_get_object_with_native_default_creates_and_returns(self, ns):
        obj = ns.get_object("new_key", 99)
        assert obj.value == 99
        assert "new_key" in ns

    def test_get_object_with_gpdc_object_default_unwraps(self, ns):
        ns["src"] = 10
        obj = ns.get_object("copy", ns.get_object("src"))
        assert obj.value == 10
        assert "copy" in ns

    def test_get_object_existing_ignores_default(self, ns):
        ns["x"] = 42
        obj = ns.get_object("x", 999)
        assert obj.value == 42
```

- [ ] **Step 7: Commit test updates**

```bash
git add tests/test_namespace.py
git commit -m "test: update tests for value-level __getitem__ and new get_object"
```

---

### Task 3: Update `test_integration.py`

**Files:**
- Modify: `tests/test_integration.py`

- [ ] **Step 1: Update `TestSpecSection12ReadScalarObject`** (line 42-58)

`ns["counter"]` now returns native value. Use `ns.get_object()` for `.type`, `.uri`:
```python
class TestSpecSection12ReadScalarObject:
    def test_read_type(self, ns):
        ns["counter"] = 42
        obj = ns.get_object("counter")
        assert obj.type == "scalar:int"

    def test_read_value(self, ns):
        ns["counter"] = 42
        assert ns["counter"] == 42

    def test_read_uri(self, ns):
        ns["counter"] = 42
        obj = ns.get_object("counter")
        assert obj.uri == "gpdc://testdomain/testns/counter"
```

- [ ] **Step 2: Update `TestSpecSection12UpdateScalarObject`** (line 61-73)

```python
class TestSpecSection12UpdateScalarObject:
    def test_update_via_namespace_setitem(self, ns):
        ns["counter"] = 42
        ns["counter"] = 100
        assert ns["counter"] == 100

    def test_update_via_obj_value_setter(self, ns):
        ns["counter"] = 42
        obj = ns.get_object("counter")
        obj.value = 100
        assert ns["counter"] == 100
```

- [ ] **Step 3: Update `TestSpecSection9CopySemantics`** (line 91-113)

```python
class TestSpecSection9CopySemantics:
    def test_assign_from_object_copies_value(self, ns):
        ns["a"] = 10
        ns["b"] = ns["a"]
        assert ns["b"] == 10

    def test_copies_are_independent(self, ns):
        ns["a"] = 10
        ns["b"] = ns["a"]
        ns["b"] = 20
        assert ns["a"] == 10
        assert ns["b"] == 20

    def test_copy_creates_separate_redis_keys(self, ns, clean_redis):
        ns["a"] = 10
        ns["b"] = ns["a"]
        key_a = make_object_key("testdomain", "testns", "a")
        key_b = make_object_key("testdomain", "testns", "b")
        assert clean_redis.exists(key_a) == 1
        assert clean_redis.exists(key_b) == 1
        assert key_a != key_b
```

- [ ] **Step 4: Update `TestSpecSection12CacheModeExamples`** (line 116-143)

```python
class TestSpecSection12CacheModeExamples:
    def test_ttl_mode_sets_expiry(self, ns, clean_redis):
        ns["temp"] = 42
        obj = ns.get_object("temp")
        obj.cache_mode = "ttl"
        obj.ttl = 60
        key = make_object_key("testdomain", "testns", "temp")
        remaining = clean_redis.ttl(key)
        assert 55 <= remaining <= 60

    def test_sliding_mode_resets_on_write(self, ns, clean_redis):
        ns["temp"] = 42
        obj = ns.get_object("temp")
        obj.cache_mode = "sliding"
        obj.ttl = 300
        obj.value = 43
        key = make_object_key("testdomain", "testns", "temp")
        remaining = clean_redis.ttl(key)
        assert 295 <= remaining <= 300

    def test_permanent_mode_no_expiry(self, ns, clean_redis):
        ns["perm"] = 42
        obj = ns.get_object("perm")
        obj.cache_mode = "permanent"
        key = make_object_key("testdomain", "testns", "perm")
        assert clean_redis.ttl(key) == -1
```

- [ ] **Step 5: Update `TestAllScalarTypes`** (line 162-184)

`ns[key]` now returns native value directly:
```python
class TestAllScalarTypes:
    @pytest.mark.parametrize(
        "key,value",
        [
            ("i", 42),
            ("f", 3.14),
            ("s", "hello world"),
            ("s_colon", "hello:world"),
            ("b_true", True),
            ("b_false", False),
            ("dt", datetime.datetime(2024, 6, 15, 9, 0, 0)),
            ("d", datetime.date(2024, 6, 15)),
            ("t", datetime.time(9, 30, 0)),
            ("n", None),
        ],
    )
    def test_roundtrip(self, ns, key, value):
        ns[key] = value
        result = ns[key]
        assert result == value
        assert type(result) is type(value)
```

- [ ] **Step 6: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: update integration tests for value-level __getitem__"
```

---

### Task 4: Update `test_spec_verification.py`

**Files:**
- Modify: `tests/test_spec_verification.py`

- [ ] **Step 1: Update assertions in `test_domain_ops`** (line 48-59)

`ns["test_int"]` etc. now return native values:
```python
        assert ns["test_int"] == 42
        assert ns["test_float"] == 42.2
        assert ns["test_bool"] == True
        assert ns["test_str"] == "hello, world"
        assert ns["test_datetime"] == datetime.datetime(2020, 1, 2)
        assert ns["test_date"] == datetime.date(2020, 1, 1)
        assert ns["test_time"] == datetime.time(0, 0, 0)
        assert ns["test_none"] is None


        test_ns.setdefault("test_setdefault", "hello, default")
        assert test_ns["test_setdefault"] == "hello, default"
```

- [ ] **Step 2: Commit**

```bash
git add tests/test_spec_verification.py
git commit -m "test: update spec verification for value-level __getitem__"
```

---

### Task 5: Run full test suite and lint

- [ ] **Step 1: Run all tests**

Run: `./venv/bin/python -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 2: Run lint and format**

Run: `./venv/bin/python -m ruff check src tests && ./venv/bin/python -m ruff format src tests`
Expected: No errors

- [ ] **Step 3: Fix any remaining failures, then commit if needed**
