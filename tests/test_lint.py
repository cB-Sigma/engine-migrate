from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine_migrate import db, lint
from engine_migrate.lint import Expression

from .conftest import EXAMPLES, check_golden, run_cli
from .test_diff import BASE, _version


def test_reads_dashboard_and_rules() -> None:
    dash = lint.read_expressions(EXAMPLES / "grafana-dashboard.json")
    assert len(dash) == 10
    assert dash[0].location == "variable model"
    assert any(e.location == "vLLM serving > Token throughput [B]" for e in dash)
    rules = lint.read_expressions(EXAMPLES / "alert-rules.yaml")
    assert [e.location for e in rules] == [
        "vllm / VllmQueueBacklog",
        "vllm / VllmAdmissionRejections",
        "vllm / VllmSlowFirstToken",
        "vllm / vllm:tokens_per_second:rate5m",
    ]


def test_prometheus_rule_crd(tmp_path: Path) -> None:
    path = tmp_path / "rule.yaml"
    path.write_text(
        "apiVersion: monitoring.coreos.com/v1\nkind: PrometheusRule\nspec:\n  groups:\n"
        "  - name: g\n    rules:\n    - alert: A\n      expr: vllm:nope > 1\n"
    )
    result = lint.lint_files([path], "vllm", "0.33.0")
    assert [p.kind for p in result.problems] == ["unknown"]


def test_suffix_resolution() -> None:
    ev = db.load("vllm", "0.33.0")
    assert lint.resolve_metric(ev, "vllm:prompt_tokens_total") is not None  # counter + _total
    assert lint.resolve_metric(ev, "vllm:time_to_first_token_seconds_bucket") is not None
    assert lint.resolve_metric(ev, "vllm:time_to_first_token_seconds_count") is not None
    assert lint.resolve_metric(ev, "vllm:num_requests_running") is not None
    assert lint.resolve_metric(ev, "vllm:num_requests_running_bucket") is None  # gauge
    assert lint.resolve_metric(ev, "vllm:num_requests_running_total") is None


def _lint(expr: str, to: str = "0.33.0", frm: str | None = None, engine: str = "vllm") -> list[lint.Problem]:
    return lint.lint_expressions([Expression("f", "loc", expr)], engine, to, frm).problems


def test_problem_kinds() -> None:
    assert _lint("vllm:num_requests_running") == []
    assert _lint("up + node_load1") == []  # not this engine's metrics
    assert [p.kind for p in _lint("vllm:does_not_exist")] == ["unknown"]
    assert [p.kind for p in _lint("rate(vllm:admission_rejections_total[5m])", to="0.31.0")] == ["not-in-version"]
    assert [p.kind for p in _lint("vllm:num_preemptions_bucket")] == ["type"]
    label = _lint('vllm:request_success_total{finish_reason="stop", job="x"}')
    assert [(p.kind, p.severity) for p in label] == [("label", "warning")]
    assert "finished_reason" in label[0].message


def test_type_change_between_versions() -> None:
    problems = _lint("rate(llamacpp:n_busy_slots_per_decode_total[5m])", to="b12323", frm="b9000", engine="llamacpp")
    assert [p.kind for p in problems] == ["type"]
    assert "counter to gauge" in problems[0].message


def test_rename_and_removal_with_from_version(monkeypatch: pytest.MonkeyPatch) -> None:
    old = _version(
        BASE,
        "1.0",
        [],
        [
            {
                "name": "vllm:latency_ms",
                "type": "histogram",
                "labels": ["model_name"],
                "help": "Histogram of request latency.",
            },
            {"name": "vllm:dropped", "type": "counter", "labels": [], "help": "Requests dropped by the old scheduler."},
            {"name": "vllm:queue", "type": "gauge", "labels": ["model_name", "pool"], "help": "Queue length"},
        ],
    )
    new = _version(
        BASE,
        "2.0",
        [],
        [
            {
                "name": "vllm:latency_seconds",
                "type": "histogram",
                "labels": ["model_name"],
                "help": "Histogram of request latency.",
            },
            {"name": "vllm:queue", "type": "gauge", "labels": ["model_name"], "help": "Queue length"},
        ],
    )
    monkeypatch.setattr(db, "load", lambda engine, version=None: {"1.0": old, "2.0": new}[version or "2.0"])
    monkeypatch.setattr(db, "versions", lambda engine: ["1.0", "2.0"])
    exprs = [
        Expression("d.json", "p1", "histogram_quantile(0.9, rate(vllm:latency_ms_bucket[5m]))"),
        Expression("d.json", "p2", "rate(vllm:dropped_total[5m])"),
        Expression("d.json", "p3", 'vllm:queue{pool="a"}'),
    ]
    result = lint.lint_expressions(exprs, "vllm", "2.0", "1.0")
    got = {p.location: p for p in result.problems}
    assert got["p1"].kind == "renamed"
    assert got["p1"].suggestion == "vllm:latency_seconds_bucket"
    assert got["p2"].kind == "removed"
    assert (got["p3"].kind, got["p3"].severity) == ("label", "error")
    assert result.exit_code() == 1


def test_golden_and_exit_codes(capsys: pytest.CaptureFixture[str]) -> None:
    files = [str(EXAMPLES / "grafana-dashboard.json"), str(EXAMPLES / "alert-rules.yaml")]
    code, out = run_cli(["lint-metrics", "--engine", "vllm", "--to", "0.31.0", *files], capsys)
    assert code == 1
    check_golden("lint-vllm-0.31.0.txt", out.replace(str(EXAMPLES) + "/", "examples/"))
    code, out = run_cli(["lint-metrics", "--engine", "vllm", "--to", "0.33.0", "-f", "json", files[1]], capsys)
    assert code == 0
    assert json.loads(out)["errors"] == 0
    code, out = run_cli(
        ["lint-metrics", "--engine", "sglang", str(EXAMPLES / "sglang-rules.yaml"), "-f", "markdown"], capsys
    )
    assert code == 1
    assert "sglang:time_per_output_token_seconds_bucket" in out


def test_strict_turns_warnings_into_failures(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "r.yaml"
    path.write_text(
        'groups:\n- name: g\n  rules:\n  - record: r\n    expr: vllm:request_success_total{finish_reason="x"}\n'
    )
    assert run_cli(["lint-metrics", "--engine", "vllm", str(path)], capsys)[0] == 0
    assert run_cli(["lint-metrics", "--engine", "vllm", "--strict", str(path)], capsys)[0] == 1
    assert run_cli(["lint-metrics", "--engine", "vllm", str(tmp_path / "missing.yaml")], capsys)[0] == 2
