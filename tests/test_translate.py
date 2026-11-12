from __future__ import annotations

import json

import pytest

from engine_migrate import db, translate
from engine_migrate.translate import APPROXIMATE, EXACT, NO_EQUIVALENT, UNKNOWN

from .conftest import EXAMPLES, check_golden, run_cli

GOLDEN_CASES = [
    ("vllm", "sglang", "vllm-serve.sh", "0.33.0", "0.5.23"),
    ("vllm", "llamacpp", "vllm-serve.sh", "0.33.0", "b12323"),
    ("vllm", "sglang", "vllm-deployment.yaml", "0.33.0", "0.5.23"),
    ("vllm", "llamacpp", "vllm-docker.sh", "0.33.0", "b12323"),
    ("vllm", "sglang", "vllm-config.yaml", "0.33.0", "0.5.23"),
    ("sglang", "vllm", "sglang-launch.sh", "0.5.18", "0.33.0"),
    ("tgi", "vllm", "tgi.env", "3.3.7", "0.33.0"),
    ("tgi", "llamacpp", "tgi.env", "3.3.7", "b12323"),
    ("vllm", "ollama", "vllm-serve.sh", "0.33.0", "0.40.16"),
    ("llamacpp", "sglang", "llamacpp-server.sh", "b12323", "0.5.23"),
]


@pytest.mark.parametrize(("src", "dst", "name", "src_v", "dst_v"), GOLDEN_CASES)
def test_golden(src: str, dst: str, name: str, src_v: str, dst_v: str, capsys: pytest.CaptureFixture[str]) -> None:
    argv = [
        "translate",
        "--from",
        src,
        "--to",
        dst,
        "--from-version",
        src_v,
        "--to-version",
        dst_v,
        str(EXAMPLES / name),
    ]
    code, out = run_cli(argv, capsys)
    assert code == 0
    check_golden(f"translate-{src}-{dst}-{name}.txt", out)


def _by_source(result: translate.Translation) -> dict[str, translate.Item]:
    return {item.source.split()[0]: item for item in result.items}


def test_statuses() -> None:
    result = translate.translate(
        "vllm serve org/model --tensor-parallel-size 4 --gpu-memory-utilization 0.8 "
        "--kv-cache-memory-bytes 1G --enforce-eager --not-a-flag 1",
        "vllm",
        "sglang",
    )
    items = _by_source(result)
    assert items["--tensor-parallel-size"].status == EXACT
    assert items["--tensor-parallel-size"].target == "--tp-size 4"
    approx = items["--gpu-memory-utilization"]
    assert approx.status == APPROXIMATE
    assert "vllm:" in approx.explanation
    assert "sglang:" in approx.explanation
    assert items["--kv-cache-memory-bytes"].status == NO_EQUIVALENT
    assert items["--enforce-eager"].status == UNKNOWN
    assert "no mapping row" in items["--enforce-eager"].explanation
    assert items["--not-a-flag"].status == UNKNOWN
    assert "Not found" in items["--not-a-flag"].explanation
    assert "--kv-cache-memory-bytes" not in result.output
    assert "--not-a-flag" not in result.output


def test_every_translated_option_is_cited() -> None:
    result = translate.translate((EXAMPLES / "vllm-serve.sh").read_text(), "vllm", "sglang")
    for item in result.items:
        assert item.source_ref.startswith("vllm@v")
        if item.status in {EXACT, APPROXIMATE}:
            assert item.target_ref.startswith("sglang@v")


def test_every_emitted_option_exists_in_the_target_database() -> None:
    for src, dst, name, src_v, dst_v in GOLDEN_CASES:
        result = translate.translate((EXAMPLES / name).read_text(), src, dst, src_v, dst_v)
        target = db.load(dst, dst_v)
        for setting in result.settings:
            if setting.origin == "positional":
                continue
            assert target.find(setting.key) is not None, (src, dst, setting.key)


def test_boolean_negation_follows_the_target_version() -> None:
    text = "python3 -m sglang.launch_server --model-path m --disable-radix-cache"
    new = translate.translate(text, "sglang", "vllm", dst_version="0.33.0")
    old = translate.translate(text, "sglang", "vllm", dst_version="0.32.0")
    assert "--disable-prefix-caching" in new.output
    assert "--no-enable-prefix-caching" in old.output


def test_option_missing_in_target_version() -> None:
    text = "python3 -m sglang.launch_server --model-path m --log-level debug"
    result = translate.translate(text, "sglang", "vllm", dst_version="0.30.0")
    item = _by_source(result)["--log-level"]
    assert item.status == NO_EQUIVALENT
    assert "0.31.0 to 0.33.0" in item.explanation


def test_option_from_another_source_version_is_explained() -> None:
    result = translate.translate("vllm serve m --log-level INFO", "vllm", "sglang", src_version="0.28.0")
    item = _by_source(result)["--log-level"]
    assert item.status == UNKNOWN
    assert "--from-version" in item.explanation


def test_value_not_accepted_by_target_is_flagged() -> None:
    result = translate.translate("vllm serve m --dtype float16", "vllm", "tgi")
    assert _by_source(result)["--dtype"].status == APPROXIMATE
    result = translate.translate("vllm serve m --dtype float32", "vllm", "tgi")
    item = _by_source(result)["--dtype"]
    assert any("not an accepted value" in n for n in item.notes)


def test_default_differences_are_reported() -> None:
    result = translate.translate("vllm serve m", "vllm", "sglang", "0.33.0", "0.5.23")
    assert any("8000" in a and "30000" in a for a in result.advisories)
    result = translate.translate("vllm serve m --port 9000", "vllm", "sglang", "0.33.0", "0.5.23")
    assert not any("Listen port" in a for a in result.advisories)


def test_json_output(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run_cli(
        ["translate", "--from", "vllm", "--to", "sglang", "-f", "json", "vllm serve m --seed 1"], capsys
    )
    assert code == 0
    data = json.loads(out)
    assert data["summary"]["EXACT"] == 2
    assert data["options"][1]["target"] == "--random-seed 1"


def test_strict_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    code, _ = run_cli(["translate", "--from", "vllm", "--to", "sglang", "--strict", "vllm serve m --bogus 1"], capsys)
    assert code == 1
    code, _ = run_cli(["translate", "--from", "vllm", "--to", "sglang", "--strict", "vllm serve m --seed 1"], capsys)
    assert code == 0
