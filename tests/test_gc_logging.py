from unittest.mock import patch, MagicMock


def test_gc_loop_logs_exceptions(domain, caplog):
    domain.get_namespace("testns")
    stop_event = MagicMock()
    stop_event.wait.side_effect = [False, True]
    domain._gc_stop_event = stop_event
    with patch("gpdatacached.gc.run_gc_for_namespace", side_effect=RuntimeError("test error")):
        domain._gc_loop()
    assert any("GPDC GC error" in record.message for record in caplog.records)
