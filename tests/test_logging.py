from github_collector.utils import logging_config


def test_setup_logging_callable():
    assert callable(logging_config.setup_logging)
