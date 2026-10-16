# tests/test_data_cache.py
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from gpdatacached.exceptions import ObjectNotFoundByURIError, GPDCError

CONFIGS_DIR = Path(__file__).parent / "configs"

MINIMAL_DOMAIN_YAML = (
    "cfg_class_name: GPDCDomainConfig\n"
    "configured_class_name: GPDCDomain\n"
    "redis_host: 127.0.0.1\n"
    "redis_port: 6379\n"
    "redis_db: 1\n"
    'redis_password: "123456"\n'
    "gc_enabled: false\n"
)


@pytest.fixture
def gpdc_config_folder():
    """A GPConfigFolder rooted at tests/configs/gpdc, built from tests/configs."""
    from gpconfig import GPConfigManager

    mgr = GPConfigManager("gpdatacached", cfg_folder=str(CONFIGS_DIR))
    return mgr.get_config("gpdc")


@pytest.fixture
def data_cache(gpdc_config_folder, clean_redis):
    """A GPDataCache over the sample configs. Closes on teardown.

    clean_redis (from conftest.py) flushes db 1 before the test, so the
    domains constructed from core_domain.yaml / edge_domain.yaml start empty.
    """
    from gpdatacached.data_cache import GPDataCache

    cache = GPDataCache(gpdc_config_folder)
    yield cache
    cache.close()


class TestConfigFolderFixture:
    def test_folder_lists_sample_domains(self, gpdc_config_folder):
        names = gpdc_config_folder.list_configs()
        assert "core_domain" in names
        assert "edge_domain" in names


class TestObjectNotFoundByURIError:
    def test_is_gpdc_error_subclass(self):
        assert issubclass(ObjectNotFoundByURIError, GPDCError)

    def test_carries_uri_and_reason(self):
        err = ObjectNotFoundByURIError("gpdc://d/ns/missing", "object not found")
        assert err.uri == "gpdc://d/ns/missing"
        assert err.reason == "object not found"
        assert "gpdc://d/ns/missing" in str(err)
        assert "object not found" in str(err)

    def test_default_reason_empty(self):
        err = ObjectNotFoundByURIError("gpdc://d/ns/x")
        assert err.reason == ""
        assert "gpdc://d/ns/x" in str(err)
        assert ":" not in str(err).split("gpdc://d/ns/x")[1]


class TestImportTimeRegistration:
    def test_gpdc_domain_is_configurable(self):
        from gpconfig import GPConfigManager
        from gpdatacached.domain import GPDCDomain

        import gpdatacached  # noqa: F401  # ensure registration ran

        assert GPConfigManager._configurable_classes.get("GPDCDomain") is GPDCDomain

    def test_gpdc_domain_config_is_registered(self):
        from gpconfig import GPConfigManager
        from gpdatacached.config import GPDCDomainConfig

        import gpdatacached  # noqa: F401  # ensure registration ran

        assert GPConfigManager._config_classes.get("GPDCDomainConfig") is GPDCDomainConfig


class TestGPDataCacheDomainManagement:
    def test_contains_known_domain(self, data_cache):
        assert "core_domain" in data_cache
        assert "edge_domain" in data_cache

    def test_contains_unknown_domain(self, data_cache):
        assert "no_such_domain" not in data_cache

    def test_get_domain_constructs_and_caches(self, data_cache):
        d1 = data_cache.get_domain("core_domain")
        d2 = data_cache.get_domain("core_domain")
        assert d1 is d2  # same cached instance

    def test_get_domain_returns_working_domain(self, data_cache):
        from gpdatacached.domain import GPDCDomain

        d = data_cache.get_domain("core_domain")
        assert isinstance(d, GPDCDomain)
        assert d.name == "core_domain"
        # The constructed domain actually works against Redis
        ns = d.get_namespace("core_app")
        ns["k"] = "v"
        assert ns["k"] == "v"

    def test_get_domain_unknown_raises_config_not_found(self, data_cache):
        from gpconfig import ConfigNotFoundError

        with pytest.raises(ConfigNotFoundError):
            data_cache.get_domain("no_such_domain")

    def test_close_closes_cached_domains(self, data_cache):
        d = data_cache.get_domain("core_domain")
        assert d._closed is False
        data_cache.close()
        assert d._closed is True

    def test_close_is_idempotent(self, data_cache):
        data_cache.get_domain("core_domain")
        data_cache.close()
        data_cache.close()  # must not raise

    def test_context_manager_closes_on_exit(self, gpdc_config_folder, clean_redis):
        from gpdatacached.data_cache import GPDataCache

        with GPDataCache(gpdc_config_folder) as cache:
            d = cache.get_domain("core_domain")
            assert d._closed is False
        assert d._closed is True


class TestGetObjectByURIRoot:
    def test_scalar_root_returns_object_not_value(self, data_cache):
        d = data_cache.get_domain("core_domain")
        ns = d.get_namespace("core_app")
        ns["counter"] = 42

        obj = data_cache.get_object_by_uri("gpdc://core_domain/core_app/counter")
        from gpdatacached import IntObject

        assert isinstance(obj, IntObject)
        assert obj.value == 42

    def test_container_root_returns_object(self, data_cache):
        d = data_cache.get_domain("core_domain")
        ns = d.get_namespace("core_app")
        ns["config"] = {"a": 1}

        obj = data_cache.get_object_by_uri("gpdc://core_domain/core_app/config")
        from gpdatacached import DictObject

        assert isinstance(obj, DictObject)

    def test_missing_scheme_raises(self, data_cache):
        with pytest.raises(ObjectNotFoundByURIError) as ei:
            data_cache.get_object_by_uri("http://d/ns/x")
        assert "gpdc://" in str(ei.value.reason)

    def test_too_few_segments_raises(self, data_cache):
        with pytest.raises(ObjectNotFoundByURIError):
            data_cache.get_object_by_uri("gpdc://d/ns")

    def test_unknown_domain_raises(self, data_cache):
        with pytest.raises(ObjectNotFoundByURIError) as ei:
            data_cache.get_object_by_uri("gpdc://no_such_domain/ns/x")
        assert ei.value.uri == "gpdc://no_such_domain/ns/x"

    def test_missing_namespace_raises(self, data_cache):
        with pytest.raises(ObjectNotFoundByURIError):
            data_cache.get_object_by_uri("gpdc://core_domain/no_such_ns/x")

    def test_missing_root_object_raises(self, data_cache):
        with pytest.raises(ObjectNotFoundByURIError) as ei:
            data_cache.get_object_by_uri("gpdc://core_domain/core_app/no_such_obj")
        assert "no_such_obj" in str(ei.value.reason) or "no_such_obj" in str(ei.value)


class TestGetObjectByURIPathTraversal:
    def test_nested_dict_path(self, data_cache):
        d = data_cache.get_domain("core_domain")
        ns = d.get_namespace("core_app")
        ns["cfg"] = {"server": {"host": "localhost", "port": 8080}}

        obj = data_cache.get_object_by_uri("gpdc://core_domain/core_app/cfg/server")
        from gpdatacached import DictObject

        assert isinstance(obj, DictObject)

    def test_deeply_nested_path(self, data_cache):
        d = data_cache.get_domain("core_domain")
        ns = d.get_namespace("core_app")
        ns["deep"] = {"a": {"b": {"c": {"d": 1}}}}

        obj = data_cache.get_object_by_uri("gpdc://core_domain/core_app/deep/a/b/c")
        from gpdatacached import DictObject

        assert isinstance(obj, DictObject)

    def test_list_numeric_index_to_container(self, data_cache):
        d = data_cache.get_domain("core_domain")
        ns = d.get_namespace("core_app")
        ns["mixed"] = [{"k": 1}, {"k": 2}]  # list of dicts

        obj = data_cache.get_object_by_uri("gpdc://core_domain/core_app/mixed/1")
        from gpdatacached import DictObject

        assert isinstance(obj, DictObject)
        assert obj["k"] == 2

    def test_scalar_terminal_in_path_raises(self, data_cache):
        d = data_cache.get_domain("core_domain")
        ns = d.get_namespace("core_app")
        ns["cfg"] = {"port": 8080}

        with pytest.raises(ObjectNotFoundByURIError) as ei:
            data_cache.get_object_by_uri("gpdc://core_domain/core_app/cfg/port")
        assert "scalar" in str(ei.value.reason).lower()

    def test_list_scalar_terminal_raises(self, data_cache):
        d = data_cache.get_domain("core_domain")
        ns = d.get_namespace("core_app")
        ns["nums"] = [10, 20, 30]

        with pytest.raises(ObjectNotFoundByURIError):
            data_cache.get_object_by_uri("gpdc://core_domain/core_app/nums/0")

    def test_list_non_numeric_index_raises(self, data_cache):
        d = data_cache.get_domain("core_domain")
        ns = d.get_namespace("core_app")
        ns["nums"] = [10, 20]

        with pytest.raises(ObjectNotFoundByURIError) as ei:
            data_cache.get_object_by_uri("gpdc://core_domain/core_app/nums/abc")
        assert "non-negative integer" in str(ei.value.reason)

    def test_missing_dict_key_in_path_raises(self, data_cache):
        d = data_cache.get_domain("core_domain")
        ns = d.get_namespace("core_app")
        ns["cfg"] = {"a": 1}

        with pytest.raises(ObjectNotFoundByURIError):
            data_cache.get_object_by_uri("gpdc://core_domain/core_app/cfg/missing")

    def test_set_descent_raises(self, data_cache):
        d = data_cache.get_domain("core_domain")
        ns = d.get_namespace("core_app")
        ns["s"] = {1, 2, 3}

        with pytest.raises(ObjectNotFoundByURIError):
            data_cache.get_object_by_uri("gpdc://core_domain/core_app/s/anything")

    def test_set_root_without_path_ok(self, data_cache):
        # SetObject as root (no path) is valid — it IS a GPDCDataObject.
        d = data_cache.get_domain("core_domain")
        ns = d.get_namespace("core_app")
        ns["s"] = {1, 2, 3}

        obj = data_cache.get_object_by_uri("gpdc://core_domain/core_app/s")
        from gpdatacached import SetObject

        assert isinstance(obj, SetObject)

    def test_path_descends_past_scalar_raises(self, data_cache):
        # tags[0] is scalar "web"; descending further must raise.
        d = data_cache.get_domain("core_domain")
        ns = d.get_namespace("core_app")
        ns["cfg"] = {"tags": ["web", "api"]}

        with pytest.raises(ObjectNotFoundByURIError):
            data_cache.get_object_by_uri("gpdc://core_domain/core_app/cfg/tags/0/extra")


class TestHasURI:
    def test_existing_scalar_root(self, data_cache):
        d = data_cache.get_domain("core_domain")
        ns = d.get_namespace("core_app")
        ns["counter"] = 42
        assert data_cache.has_uri("gpdc://core_domain/core_app/counter") is True

    def test_existing_container_root(self, data_cache):
        d = data_cache.get_domain("core_domain")
        ns = d.get_namespace("core_app")
        ns["cfg"] = {"a": 1}
        assert data_cache.has_uri("gpdc://core_domain/core_app/cfg") is True

    def test_existing_nested_path(self, data_cache):
        d = data_cache.get_domain("core_domain")
        ns = d.get_namespace("core_app")
        ns["cfg"] = {"server": {"host": "x"}}
        assert data_cache.has_uri("gpdc://core_domain/core_app/cfg/server") is True

    def test_missing_root(self, data_cache):
        assert data_cache.has_uri("gpdc://core_domain/core_app/no_such") is False

    def test_missing_domain(self, data_cache):
        assert data_cache.has_uri("gpdc://no_such_domain/ns/x") is False

    def test_missing_namespace(self, data_cache):
        assert data_cache.has_uri("gpdc://core_domain/no_such_ns/x") is False

    def test_scalar_terminal(self, data_cache):
        d = data_cache.get_domain("core_domain")
        ns = d.get_namespace("core_app")
        ns["cfg"] = {"port": 8080}
        assert data_cache.has_uri("gpdc://core_domain/core_app/cfg/port") is False

    def test_broken_path(self, data_cache):
        d = data_cache.get_domain("core_domain")
        ns = d.get_namespace("core_app")
        ns["cfg"] = {"a": 1}
        assert data_cache.has_uri("gpdc://core_domain/core_app/cfg/missing") is False

    def test_malformed_uri(self, data_cache):
        assert data_cache.has_uri("http://d/ns/x") is False

    def test_too_few_segments(self, data_cache):
        assert data_cache.has_uri("gpdc://d/ns") is False

    def test_propagates_non_not_found_errors(self, data_cache):
        # has_uri must swallow ONLY ObjectNotFoundByURIError. A closed cache
        # raises RuntimeError from _check_open() — which is not an
        # ObjectNotFoundByURIError — so it must propagate, not return False.
        data_cache.close()
        with pytest.raises(RuntimeError):
            data_cache.has_uri("gpdc://core_domain/core_app/anything")


class TestCrossDomainIntegration:
    def test_two_domains_coexist(self, data_cache):
        d_core = data_cache.get_domain("core_domain")
        d_edge = data_cache.get_domain("edge_domain")
        assert d_core is not d_edge
        assert d_core.name == "core_domain"
        assert d_edge.name == "edge_domain"

    def test_uri_is_domain_scoped(self, data_cache):
        # Write to core_domain, ensure edge_domain doesn't see it via URI.
        d_core = data_cache.get_domain("core_domain")
        ns_core = d_core.get_namespace("core_app")
        ns_core["secret"] = "core"

        # edge_blob namespace in edge_domain is empty for this object.
        with pytest.raises(ObjectNotFoundByURIError):
            data_cache.get_object_by_uri("gpdc://edge_domain/edge_blob/secret")

    def test_has_uri_and_get_object_agree(self, data_cache):
        d = data_cache.get_domain("edge_domain")
        ns = d.get_namespace("edge_session")
        ns["nested"] = {"inner": {"deep": [1, 2, 3]}}

        # Matrix of agreement
        cases = [
            ("gpdc://edge_domain/edge_session/nested", True),
            ("gpdc://edge_domain/edge_session/nested/inner", True),
            # deep is a dict; deep/nosuch missing
            ("gpdc://edge_domain/edge_session/nested/inner/nosuch", False),
            # deep contains list [1,2,3]; deep/0 is scalar 1 -> not an object
            ("gpdc://edge_domain/edge_session/nested/inner/deep/0", False),
            ("gpdc://edge_domain/edge_session/missing", False),
            ("gpdc://nope/ns/x", False),
        ]
        for uri, expected in cases:
            assert data_cache.has_uri(uri) is expected, f"has_uri mismatch for {uri}"
            if expected:
                obj = data_cache.get_object_by_uri(uri)
                assert obj is not None
            else:
                with pytest.raises(ObjectNotFoundByURIError):
                    data_cache.get_object_by_uri(uri)

    def test_get_domain_unknown_after_successful_get(self, data_cache):
        # Cache is populated by a successful get; an unknown domain still raises.
        data_cache.get_domain("core_domain")
        from gpconfig import ConfigNotFoundError

        with pytest.raises(ConfigNotFoundError):
            data_cache.get_domain("no_such_domain")


class TestGPDataCacheContainsContract:
    """CQR-2026-07-24 inconsistency.03: pin the documented `__contains__` semantics
    (a pure config-snapshot check — independent of instance creation AND of close())
    and the close() behaviour of the resource-touching methods. No code change —
    the existing behaviour is the intended design; these tests pin it."""

    def test_contains_reflects_config_not_instance(self, data_cache):
        # __contains__ is True for a configured domain BEFORE any instance exists.
        data_cache._domains.clear()  # ensure no instance cached
        assert "core_domain" not in data_cache._domains
        assert "core_domain" in data_cache

    def test_contains_unchanged_after_instance_created(self, data_cache):
        assert "core_domain" in data_cache
        data_cache.get_domain("core_domain")  # forces lazy construction
        assert "core_domain" in data_cache  # still True

    def test_contains_false_for_missing_config(self, data_cache):
        assert "no_such_domain" not in data_cache

    def test_lazy_get_domain_creates_and_caches_instance(self, data_cache):
        data_cache._domains.clear()
        d1 = data_cache.get_domain("core_domain")
        d2 = data_cache.get_domain("core_domain")
        assert d1 is d2  # second access returns the same cached instance

    def test_contains_works_after_close(self, gpdc_config_folder, clean_redis):
        # The deliberate exception: membership is a config-snapshot check and
        # touches no resources, so it remains valid after close().
        from gpdatacached.data_cache import GPDataCache

        cache = GPDataCache(gpdc_config_folder)
        cache.get_domain("core_domain")
        assert "core_domain" in cache
        cache.close()
        assert "core_domain" in cache  # still works — config snapshot, not resources

    def test_resource_methods_raise_after_close(self, gpdc_config_folder, clean_redis):
        # The resource-touching methods all raise RuntimeError after close(),
        # in contrast to __contains__ (the documented exception).
        from gpdatacached.data_cache import GPDataCache

        cache = GPDataCache(gpdc_config_folder)
        cache.get_domain("core_domain")
        cache.close()
        with pytest.raises(RuntimeError, match="closed"):
            cache.get_domain("core_domain")
        with pytest.raises(RuntimeError, match="closed"):
            cache.get_object_by_uri("gpdc://core_domain/core_app/x")
        with pytest.raises(RuntimeError, match="closed"):
            cache.has_uri("gpdc://core_domain/core_app/x")


def make_config_folder(tmp_path: Path, gpdc_files: dict[str, str]) -> object:
    """Build a GPConfigFolder over a temp config tree with the given gpdc/*.yaml files."""
    from gpconfig import GPConfigManager

    (tmp_path / "global_env.yaml").write_text("project: gpdatacached\nenvironment: test\n")
    (tmp_path / "gpdc").mkdir()
    for fname, content in gpdc_files.items():
        (tmp_path / "gpdc" / fname).write_text(content)
    return GPConfigManager("gpdatacached", cfg_folder=str(tmp_path)).get_config("gpdc")


class TestDottedDomainAtEscape:
    """'@' in a domain config filename stands for '.' in the domain name
    (DOMAIN_DOT_ESCAPE): domain_name@com.yaml configures "domain_name.com"."""

    def test_get_domain_unescapes_at_to_dot(self, data_cache):
        d = data_cache.get_domain("domain_name.com")
        assert d.name == "domain_name.com"

    def test_get_domain_caches_under_dotted_name(self, data_cache):
        d1 = data_cache.get_domain("domain_name.com")
        d2 = data_cache.get_domain("domain_name.com")
        assert d1 is d2

    def test_dotted_domain_redis_keys_use_dots(self, data_cache, raw_redis):
        data_cache.get_domain("domain_name.com")
        keys = [k for k in raw_redis.scan_iter("gpdc:domain_name.com:*")]
        assert keys, "expected keys under the dotted domain name"

    def test_get_object_by_uri_dotted_domain(self, data_cache):
        d = data_cache.get_domain("domain_name.com")
        ns = d.ensure_namespace("site.data")
        ns["page.rank"] = {"score": 1}

        obj = data_cache.get_object_by_uri("gpdc://domain_name.com/site.data/page.rank")
        assert obj.value == {"score": 1}
        assert data_cache.has_uri("gpdc://domain_name.com/site.data/page.rank") is True

    def test_contains_dotted_domain_true_at_name_false(self, data_cache):
        assert "domain_name.com" in data_cache
        # '@' is not a legal GPDC name character — must never match.
        assert "domain_name@com" not in data_cache

    def test_get_domain_rejects_at_in_name(self, data_cache):
        with pytest.raises(ValueError, match="domain name"):
            data_cache.get_domain("domain_name@com")

    def test_uri_with_at_domain_raises_not_found(self, data_cache):
        # URI failures must surface as ObjectNotFoundByURIError only — the
        # ValueError raised inside get_domain must not leak through.
        uri = "gpdc://domain_name@com/site.data/x"
        with pytest.raises(ObjectNotFoundByURIError) as ei:
            data_cache.get_object_by_uri(uri)
        assert "invalid domain name" in ei.value.reason
        assert data_cache.has_uri(uri) is False

    def test_missing_dotted_domain_not_found(self, data_cache):
        from gpconfig import ConfigNotFoundError

        with pytest.raises(ConfigNotFoundError):
            data_cache.get_domain("no.such.domain")
        uri = "gpdc://no.such.domain/ns/x"
        with pytest.raises(ObjectNotFoundByURIError):
            data_cache.get_object_by_uri(uri)
        assert data_cache.has_uri(uri) is False

    def test_plain_domain_still_resolves_unescaped_config(self, data_cache):
        # Backward compatibility: dot-free names load exactly as before.
        d = data_cache.get_domain("core_domain")
        assert d.name == "core_domain"


class TestFactoryPathAtEscape:
    """gpconfig's object factory constructs GPDCDomain from '@'-named configs:
    GPDCDomain's own name resolution restores '@' -> '.' (stem-derived names
    only; an explicit name= containing '@' is still rejected)."""

    def test_factory_constructs_dotted_domain(self, gpdc_config_folder, clean_redis):
        d = gpdc_config_folder.get_object("domain_name@com")
        try:
            assert type(d).__name__ == "GPDCDomain"
            assert d.name == "domain_name.com"  # restored, not the raw stem
        finally:
            d.close()

    def test_factory_and_gpdatacache_agree_on_name(self, data_cache, gpdc_config_folder, clean_redis):
        via_cache = data_cache.get_domain("domain_name.com")
        via_factory = gpdc_config_folder.get_object("domain_name@com")
        try:
            assert via_cache.name == via_factory.name == "domain_name.com"
        finally:
            via_factory.close()

    def test_explicit_name_with_at_rejected(self, domain_config, clean_redis):
        # '@' in an EXPLICIT name is a caller error: the escape exists only to
        # smuggle dots through filenames, and explicit names can just use '.'.
        from gpdatacached.domain import GPDCDomain

        with pytest.raises(ValueError, match="domain name"):
            GPDCDomain(domain_config, name="x@y")

    def test_explicit_name_matching_escaped_config(self, gpdc_config_folder, clean_redis):
        # name="domain_name.com" must not "mismatch" the @-stem: the restore
        # happens before the comparison.
        from gpdatacached.domain import GPDCDomain

        config = gpdc_config_folder.get_config("domain_name@com")
        d = GPDCDomain(config, name="domain_name.com")
        try:
            assert d.name == "domain_name.com"
        finally:
            d.close()

    def test_explicit_name_conflicting_with_escaped_config(self, gpdc_config_folder, clean_redis):
        from gpdatacached.domain import GPDCDomain

        config = gpdc_config_folder.get_config("domain_name@com")
        with pytest.raises(ValueError, match="mismatch"):
            GPDCDomain(config, name="other.name")


class TestDottedConfigStemWarning:
    """GPDataCache.__init__ warns once about config stems containing '.' —
    they are listed by list_configs() but unaddressable via gpconfig."""

    def test_init_warns_for_dotted_stem(self, tmp_path, caplog):
        from gpdatacached.data_cache import GPDataCache

        folder = make_config_folder(tmp_path, {"bad.name.yaml": MINIMAL_DOMAIN_YAML})
        with caplog.at_level(logging.WARNING, logger="gpdatacached.data_cache"):
            GPDataCache(folder)
        warns = [r for r in caplog.records if "bad.name" in r.getMessage()]
        assert len(warns) == 1
        assert "bad@name" in warns[0].getMessage()  # suggests the @-spelling

    def test_init_no_warning_for_at_stem(self, tmp_path, caplog):
        from gpdatacached.data_cache import GPDataCache

        folder = make_config_folder(tmp_path, {"domain_name@com.yaml": MINIMAL_DOMAIN_YAML})
        with caplog.at_level(logging.WARNING, logger="gpdatacached.data_cache"):
            GPDataCache(folder)
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    def test_subfolder_shadowing_config_name_raises_not_found(self, tmp_path):
        # A directory named like the escaped config: get_config returns a
        # GPConfigFolder, which must surface as ConfigNotFoundError.
        from gpconfig import ConfigNotFoundError
        from gpdatacached.data_cache import GPDataCache

        folder = make_config_folder(tmp_path, {})
        (tmp_path / "gpdc" / "nope@x").mkdir()
        cache = GPDataCache(folder)
        with pytest.raises(ConfigNotFoundError):
            cache.get_domain("nope.x")
