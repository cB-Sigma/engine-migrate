from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
GOLDEN = ROOT / "tests" / "golden"

# The extractors live outside the installed package.
sys.path.insert(0, str(ROOT))


def run_cli(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    from engine_migrate.cli import main

    code = main(argv)
    return code, capsys.readouterr().out


def check_golden(name: str, actual: str) -> None:
    """Compare with tests/golden/<name>; set UPDATE_GOLDEN=1 to rewrite."""
    path = GOLDEN / name
    if os.environ.get("UPDATE_GOLDEN"):
        path.write_text(actual, encoding="utf-8")
    assert path.exists(), f"missing golden file {path}; run with UPDATE_GOLDEN=1"
    assert actual == path.read_text(encoding="utf-8")
