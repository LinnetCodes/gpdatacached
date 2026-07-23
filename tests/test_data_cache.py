# tests/test_data_cache.py
from __future__ import annotations

from pathlib import Path

import pytest

from gpdatacached.exceptions import ObjectNotFoundByURIError, GPDCError

CONFIGS_DIR = Path(__file__).parent / "configs"


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
