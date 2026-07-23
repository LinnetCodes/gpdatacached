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

        import gpdatacached  # ensure registration ran
        assert GPConfigManager._configurable_classes.get("GPDCDomain") is GPDCDomain

    def test_gpdc_domain_config_is_registered(self):
        from gpconfig import GPConfigManager
        from gpdatacached.config import GPDCDomainConfig

        import gpdatacached  # ensure registration ran
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
