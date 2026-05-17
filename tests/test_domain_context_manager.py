def test_domain_as_context_manager(clean_redis, domain_config):
    from gpdatacached.domain import GPDCDomain

    with GPDCDomain("testdomain", domain_config) as domain:
        assert domain.name == "testdomain"
        ns = domain.get_namespace("testns")
        ns["x"] = 42
        assert ns["x"] == 42
    assert domain._gc_thread is None or not domain._gc_thread.is_alive()


def test_domain_context_manager_closes_on_exception(clean_redis, domain_config):
    from gpdatacached.domain import GPDCDomain

    domain = None
    try:
        with GPDCDomain("testdomain", domain_config) as d:
            domain = d
            raise ValueError("test error")
    except ValueError:
        pass
    assert domain._gc_thread is None or not domain._gc_thread.is_alive()
