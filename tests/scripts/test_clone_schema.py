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


def test_portable_ddl_drops_the_counter_when_the_comment_has_a_bracket(module):
    # rpartition(")") found the bracket inside the comment and kept the counter (final review).
    ddl = (
        "CREATE TABLE `t` (\n  `id` int NOT NULL AUTO_INCREMENT\n"
        ") ENGINE=InnoDB AUTO_INCREMENT=42 DEFAULT CHARSET=utf8mb4 COMMENT='log (rollback)'"
    )
    assert "AUTO_INCREMENT=" not in module.portable_ddl(ddl)


def test_check_target_treats_localhost_and_127_0_0_1_as_the_same_host(module):
    source = "mysql+mysqlconnector://user@localhost:3307/github_data"
    with pytest.raises(ValueError):
        module.check_target(source, "mysql+mysqlconnector://gh_test@127.0.0.1:3307/github_data")


class FakeConnection:
    def __init__(self, fail_on):
        self.fail_on, self.executed = fail_on, []

    def execute(self, statement):
        sql = str(statement)
        self.executed.append(sql)
        if self.fail_on in sql:
            raise RuntimeError("syntax error")


def test_failed_create_removes_the_tables_created_before(module):
    # MySQL commits each CREATE TABLE at once, so the transaction does not undo them; a partial
    # schema would make the next run refuse the target as not empty.
    conn = FakeConnection(fail_on="CREATE TABLE `b`")

    with pytest.raises(RuntimeError):
        module.create_tables(
            conn, ["a", "b"], ["CREATE TABLE `a` (x int)", "CREATE TABLE `b` (x int)"]
        )

    assert "DROP TABLE `a`" in conn.executed
    assert conn.executed[-1] == "SET FOREIGN_KEY_CHECKS = 1"
