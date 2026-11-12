from __future__ import annotations

import pytest
import yaml

from engine_migrate import db, shapes
from engine_migrate.errors import ConfigParseError

from .conftest import EXAMPLES

VLLM = db.load("vllm", "0.33.0")


@pytest.mark.parametrize(
    ("name", "shape"),
    [
        ("vllm-serve.sh", "command"),
        ("vllm-docker.sh", "docker"),
        ("vllm-deployment.yaml", "kubernetes"),
        ("vllm-config.yaml", "yaml"),
        ("tgi.env", "env"),
        ("llamacpp-server.sh", "command"),
    ],
)
def test_detect_shape(name: str, shape: str) -> None:
    assert shapes.detect_shape((EXAMPLES / name).read_text()) == shape


def test_command_parsing() -> None:
    parsed = shapes.parse("FOO=1 vllm serve org/m -tp 2 --api-key a b --trust-remote-code --max-model-len=4096", VLLM)
    assert parsed.prefix == ["vllm", "serve"]
    got = [(s.key, s.values, s.origin) for s in parsed.settings]
    assert got == [
        ("FOO", ["1"], "env"),
        ("--model", ["org/m"], "positional"),
        ("-tp", ["2"], "arg"),
        ("--api-key", ["a", "b"], "arg"),
        ("--trust-remote-code", [], "arg"),
        ("--max-model-len", ["4096"], "arg"),
    ]


def test_python_module_launcher() -> None:
    parsed = shapes.parse("python3 -m sglang.launch_server --model-path m --port 1", db.load("sglang"))
    assert parsed.prefix == ["python3", "-m", "sglang.launch_server"]
    assert [s.key for s in parsed.settings] == ["--model-path", "--port"]


def test_docker_keeps_docker_options() -> None:
    parsed = shapes.parse((EXAMPLES / "vllm-docker.sh").read_text(), VLLM)
    assert parsed.envelope["image"] == "vllm/vllm-openai:v0.33.0"
    assert ["-e", "HF_TOKEN=$HF_TOKEN"] in parsed.envelope["options"]
    assert parsed.settings[0].key == "--model"


def test_kubernetes_env_and_args() -> None:
    parsed = shapes.parse((EXAMPLES / "vllm-deployment.yaml").read_text(), VLLM)
    keys = [s.key for s in parsed.settings]
    assert keys[0] == "VLLM_LOGGING_LEVEL"
    assert "--lora-modules" in keys
    lora = next(s for s in parsed.settings if s.key == "--lora-modules")
    assert lora.values == ["sql=/adapters/sql", "support=/adapters/support"]


def test_kubernetes_output_is_valid_yaml_with_untouched_fields() -> None:
    from engine_migrate import translate

    result = translate.translate((EXAMPLES / "vllm-deployment.yaml").read_text(), "vllm", "sglang")
    doc = yaml.safe_load(result.output)
    container = doc["spec"]["template"]["spec"]["containers"][0]
    assert container["resources"] == {"limits": {"nvidia.com/gpu": 2}}
    assert container["command"] == ["python3", "-m", "sglang.launch_server"]
    assert container["env"][0]["name"] == "HF_TOKEN"
    assert container["image"] == "<sglang-image>"


def test_yaml_booleans() -> None:
    parsed = shapes.parse("enable-prefix-caching: false\ntrust_remote_code: true\n", VLLM, "yaml")
    assert [(s.key, s.enabled) for s in parsed.settings] == [
        ("--enable-prefix-caching", False),
        ("--trust-remote-code", True),
    ]


def test_env_form_of_flags() -> None:
    parsed = shapes.parse("MODEL_ID=a/b\nPORT=1\n", db.load("tgi"))
    assert [(s.key, s.written) for s in parsed.settings] == [("--model-id", "MODEL_ID"), ("--port", "PORT")]


def test_quote_keeps_variables_expandable() -> None:
    assert shapes.quote("HF_TOKEN=$HF_TOKEN") == "HF_TOKEN=$HF_TOKEN"
    assert shapes.quote("a b") == "'a b'"
    assert shapes.quote('{"a": 1}') == "'{\"a\": 1}'"


def test_errors() -> None:
    with pytest.raises(ConfigParseError):
        shapes.detect_shape("   ")
    with pytest.raises(ConfigParseError):
        shapes.parse("docker run --rm", VLLM, "docker")
    with pytest.raises(ConfigParseError):
        shapes.parse("- a\n- b\n", VLLM, "yaml")
