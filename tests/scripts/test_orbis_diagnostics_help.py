"""`--help` of the Orbis diagnostics shows the help and opens no connection.

Functional test 2026-10-20: scripts without an argument parser ignored `--help` and started
their queries against Orbis right away.
"""

import importlib.util
from pathlib import Path

import pytest

DIAGNOSTICS = Path(__file__).parent.parent.parent / "scripts" / "diagnostics"


@pytest.mark.parametrize("script", ["diagnose_hq_signals.py", "check_orbis_schema.py"])
def test_help_does_not_connect(script, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location(script[:-3], DIAGNOSTICS / script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def no_connection(*args, **kwargs):
        raise AssertionError("--help must not connect to Orbis")

    monkeypatch.setattr(module, "OrbisClient", no_connection)
    with pytest.raises(SystemExit) as exit_info:
        module.main(["--help"])
    assert exit_info.value.code == 0
    assert "usage" in capsys.readouterr().out.lower()
