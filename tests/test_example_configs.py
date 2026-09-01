"""Every config we ship must actually load.

A shipped example with a key the code does not accept is worse than no example:
it is copied verbatim and fails at the first run, on someone else's machine,
against a database we cannot see. `matrix-cosec-verified.example.yaml` shipped
with `duplicate_window_seconds` where PairingPolicy expects `dedup_seconds`,
which is exactly the failure this guards.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from matrixreports.config import Config

CONFIGS = sorted((Path(__file__).parent.parent / "config").glob("*.yaml"))


def test_there_are_example_configs_to_check():
    assert CONFIGS, "no example configs found to validate"


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.name)
def test_example_config_loads(path: Path):
    Config.load(path)


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.name)
def test_example_config_ships_no_credentials(path: Path):
    """A public repo must never carry a real host or password."""
    config = Config.load(path)
    assert not (config.database.password or "").strip()
    assert "PWD=" not in (config.database.dsn or "").replace("PWD=<password>", "")
