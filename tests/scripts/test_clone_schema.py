"""Tests for scripts/maintenance/clone_schema.py.

Only the pure helpers are tested here; copying the tables needs two MySQL servers and is
checked when the test instance is set up (docs/usage.md, "Test Instance").
"""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "maintenance" / "clone_schema.py"

SOURCE = "mysql+mysqlconnector://user@localhost:3306/github_data"
TARGET = "mysql+mysqlconnector://gh_test@127.0.0.1:3307/github_data"


@pytest.fixture(scope="module")
def module():
    spec = importlib.util.spec_from_file_location("clone_schema_under_test", SCRIPT)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_portable_ddl_drops_the_table_counter_only(module):
    ddl = (
        "CREATE TABLE `repositories` (\n"
        "  `id` int NOT NULL AUTO_INCREMENT,\n"
        "  PRIMARY KEY (`id`)\n"
        ") ENGINE=InnoDB AUTO_INCREMENT=25712813 DEFAULT CHARSET=utf8mb4"
    )

    result = module.portable_ddl(ddl)

    assert "`id` int NOT NULL AUTO_INCREMENT," in result
    assert "AUTO_INCREMENT=" not in result
    assert result.endswith(") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")


def test_portable_ddl_without_counter_is_unchanged(module):
    ddl = "CREATE TABLE `t` (\n  `id` int NOT NULL\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
    assert module.portable_ddl(ddl) == ddl


@pytest.mark.parametrize(
    "target",
    [
        "mysql+mysqlconnector://gh_test@127.0.0.1:3306/github_data",
        "mysql+mysqlconnector://gh_test@127.0.0.1/github_data",  # no port means 3306
        SOURCE,
    ],
)
def test_check_target_refuses_the_production_port_and_the_source(module, target):
    with pytest.raises(ValueError):
        module.check_target(SOURCE, target)


def test_check_target_accepts_the_test_instance(module):
    module.check_target(SOURCE, TARGET)
