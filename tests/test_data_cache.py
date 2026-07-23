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
