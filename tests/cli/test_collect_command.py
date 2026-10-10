from github_collector.cli import collect_command


def test_main_exists():
    assert callable(collect_command.main)
