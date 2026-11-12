from __future__ import annotations

import json
import subprocess
import sys

import pytest

from engine_migrate import __version__

from .conftest import check_golden, run_cli


def test_demo_runs(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run_cli(["--demo"], capsys)
    assert code == 0
    assert "$ engine-migrate translate --from vllm --to sglang" in out
    assert "$ engine-migrate diff sglang 0.5.18 0.5.23" in out
    assert "$ engine-migrate lint-metrics --engine vllm" in out


def test_no_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run_cli([], capsys)
    assert code == 2
    assert "translate" in out


def test_module_entry_point() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "engine_migrate", "--version"], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == f"engine-migrate {__version__}"


def test_flags_browse(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run_cli(["flags", "vllm", "0.33.0", "--grep", "prefix-caching", "--kind", "cli"], capsys)
    assert code == 0
    check_golden("flags-vllm-prefix.txt", out)
    code, out = run_cli(["flags", "sglang", "--deprecated", "-f", "json"], capsys)
    data = json.loads(out)
    assert data["options"]
    assert all(o["deprecated"] for o in data["options"])
    code, out = run_cli(["flags", "ollama", "--kind", "param", "-f", "markdown"], capsys)
    assert "`num_ctx`" in out


def test_metrics_browse(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run_cli(["metrics", "llamacpp", "b12323"], capsys)
    assert code == 0
    check_golden("metrics-llamacpp.txt", out)
    code, out = run_cli(["metrics", "ollama"], capsys)
    assert "does not expose" in out
    code, out = run_cli(["metrics", "tgi", "--grep", "queue", "-f", "json"], capsys)
    assert {m["name"] for m in json.loads(out)["metrics"]} >= {"tgi_queue_size"}


def test_versions(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run_cli(["versions"], capsys)
    assert code == 0
    assert "vLLM" in out
    assert "0.33.0" in out


def test_errors_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert run_cli(["flags", "triton"], capsys)[0] == 2
    assert run_cli(["translate", "--from", "vllm", "--to", "sglang", "missing-file.sh"], capsys)[0] == 2
    assert run_cli(["flags", "vllm", "0.1.0"], capsys)[0] == 2
