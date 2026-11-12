from __future__ import annotations

import pytest

from engine_migrate.promql import Selector, extract_selectors, metric_names

CASES = [
    ("vllm:num_requests_running", ["vllm:num_requests_running"]),
    ("sum(rate(vllm:prompt_tokens_total[5m]))", ["vllm:prompt_tokens_total"]),
    ('sum by (model_name) (vllm:num_requests_waiting{model_name=~"$model"})', ["vllm:num_requests_waiting"]),
    ("sum without(instance)(rate(a_total[1m])) / ignoring(code) group_left sum(b)", ["a_total", "b"]),
    (
        "histogram_quantile(0.95, sum by (le) (rate(vllm:e2e_request_latency_seconds_bucket[$__rate_interval])))",
        ["vllm:e2e_request_latency_seconds_bucket"],
    ),
    ("rate(x_sum[5m]) / rate(x_count[5m]) > 1.5e3", ["x_sum", "x_count"]),
    ('{__name__="tgi_request_count", job="tgi"}', ["tgi_request_count"]),
    ('label_replace(up{job="a"}, "dst", "$1", "src", "(.*)") and on (instance) node_load1', ["up", "node_load1"]),
    ("avg_over_time(foo[5m:1m] offset 1h) or vector(0)", ["foo"]),
    (
        'sum(rate(http_requests_total{code=~"5.."}[${interval}])) by (job) unless on(job) absent(up)',
        ["http_requests_total", "up"],
    ),
    ("# a comment mentioning fake_metric\nreal_metric * 100", ["real_metric"]),
    ("count(count by (pod) (kube_pod_info)) > bool 3", ["kube_pod_info"]),
    ("time() - process_start_time_seconds", ["process_start_time_seconds"]),
    (
        "topk(5, sum by (reason) (increase(sglang:num_aborted_requests_total[1h])))",
        ["sglang:num_aborted_requests_total"],
    ),
    ("1", []),
]


@pytest.mark.parametrize(("expr", "expected"), CASES)
def test_metric_names(expr: str, expected: list[str]) -> None:
    assert metric_names(expr) == expected


def test_label_matchers_are_attached_to_their_metric() -> None:
    selectors = extract_selectors('sum(a{x="1", y=~"b|c"}) / sum(b{z!="q"}) + c')
    assert selectors == [Selector("a", ("x", "y")), Selector("b", ("z",)), Selector("c", ())]


def test_strings_and_durations_are_not_metrics() -> None:
    assert metric_names('count_values("version", build_info) + on() group_left() vector(5m)') == ["build_info"]
