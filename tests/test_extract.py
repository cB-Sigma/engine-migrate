"""Tests for the static extractors in tools/extract.

The unit tests run everywhere on small inline sources.  The re-extraction
tests need the reference clones; point ENGINE_MIGRATE_REF at the directory
that holds them (see docs/database.md) or they are skipped.
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import pytest
from tools.extract import llamacpp, ollama, run, tgi
from tools.extract import vllm as vllm_extract
from tools.extract.common import Repo, Scope, literal_values, looks_deprecated, static_str, static_value
from tools.extract.pyargs import analyse_annotation, collect_classes, option_from_add_argument

from .conftest import ROOT

REF = Path(os.environ.get("ENGINE_MIGRATE_REF", ""))
HAVE_REF = bool(os.environ.get("ENGINE_MIGRATE_REF")) and REF.is_dir()

# One pinned tag per engine is re-extracted and compared with the committed file.
PINNED = [
    ("vllm", "v0.33.0"),
    ("sglang", "v0.5.23"),
    ("llamacpp", "b12323"),
    ("tgi", "v3.3.7"),
    ("ollama", "v0.40.16"),
]


@pytest.mark.skipif(not HAVE_REF, reason="set ENGINE_MIGRATE_REF to the directory with the reference clones")
@pytest.mark.parametrize(("engine", "tag"), PINNED)
def test_reextraction_reproduces_the_committed_database(engine: str, tag: str) -> None:
    if not (REF / run.ENGINES[engine]["clone"]).is_dir():
        pytest.skip(f"no clone of {engine} under {REF}")
    fresh = run.build(engine, tag, REF)
    committed = json.loads((run.DB_DIR / engine / f"{run.version_of(tag)}.json").read_text(encoding="utf-8"))
    assert fresh == committed


def test_pins_match_the_committed_database() -> None:
    for engine, tags in run.PINS.items():
        files = {p.stem for p in (run.DB_DIR / engine).glob("*.json")}
        assert files == {run.version_of(t) for t in tags}, engine


def test_validation_report_covers_every_pin() -> None:
    report = json.loads((ROOT / "docs" / "extraction-validation.json").read_text(encoding="utf-8"))
    for engine, tags in run.PINS.items():
        assert set(report[engine]) == {run.version_of(t) for t in tags}
        for result in report[engine].values():
            assert "checks" in result, engine


# -- Python helpers ---------------------------------------------------------


def _scope(src: str) -> tuple[ast.Module, Scope]:
    tree = ast.parse(src)
    scope = Scope()
    scope.add_module(tree)
    return tree, scope


def test_static_evaluation() -> None:
    _tree, scope = _scope(
        'A = 4\nB = ["x", "y"]\nC = f"up to {A} of {B}"\nD = Literal["p", Inner]\nInner = Literal["q"]\n'
    )
    assert static_value(ast.parse("A * 1024").body[0].value, scope) == 4096  # type: ignore[attr-defined]
    assert static_str(scope.names["C"], scope) == "up to 4 of ['x', 'y']"
    assert literal_values(scope.names["D"], scope) == ["p", "q"]
    assert static_str(ast.parse('f"n={unknown}"').body[0].value, scope) == "n={unknown}"  # type: ignore[attr-defined]


def test_annotation_analysis() -> None:
    tree, scope = _scope("Mode = Literal['a', 'b']\n")
    classes = collect_classes(tree, "x.py")

    def hint(src: str) -> set[str]:
        return analyse_annotation(ast.parse(src, mode="eval").body, scope, classes).atoms

    assert hint("int | None") == {"int", "none"}
    assert hint("Optional[list[str]]") == {"none", "list"}
    assert hint("Mode | str") == {"literal", "str"}
    assert hint("bool") == {"bool"}


def test_add_argument_record() -> None:
    src = (
        """parser.add_argument("--tp-size", "-tp", type=int, default=Args.tp, choices=[1, 2], help="Tensor %% size.")"""
    )
    call = ast.parse(src).body[0].value  # type: ignore[attr-defined]
    rec = option_from_add_argument(call, Scope(), "f.py", class_defaults={"tp": ast.Constant(1)})
    assert rec is not None
    assert (rec["name"], rec["aliases"], rec["type"], rec["default"], rec["choices"], rec["help"]) == (
        "--tp-size",
        ["-tp"],
        "int",
        1,
        [1, 2],
        "Tensor % size.",
    )
    flag = ast.parse('p.add_argument("--old", action="store_true", help="(Deprecated) use --new")').body[0].value  # type: ignore[attr-defined]
    rec = option_from_add_argument(flag, Scope(), "f.py")
    assert rec is not None
    assert rec["type"] == "bool"
    assert rec["deprecated"]


def test_deprecation_heuristic() -> None:
    assert looks_deprecated("Deprecated. Use --x instead.")
    assert looks_deprecated("(Deprecated: use --y) Enable thing.")
    assert looks_deprecated("--a is deprecated and will be removed in v1.")
    assert not looks_deprecated("Whether to allow deprecated quantization methods.")


def test_vllm_negations() -> None:
    assert vllm_extract.negations_for("--enable-x", True) == ["--disable-x", "--no-enable-x"]
    assert vllm_extract.negations_for("--enable-x", False) == ["--no-enable-x"]
    assert vllm_extract.negations_for("--trust", True) == ["--no-trust"]


class _FakeRepo(Repo):
    """A Repo that serves files from a dict instead of git."""

    def __init__(self, files: dict[str, str]) -> None:
        object.__setattr__(self, "path", Path("."))
        object.__setattr__(self, "tag", "t")
        object.__setattr__(self, "_files", files)

    def read(self, relpath: str) -> str:  # type: ignore[override]
        from tools.extract.common import SourceError

        try:
            return self._files[relpath]  # type: ignore[attr-defined,no-any-return]
        except KeyError:
            raise SourceError(relpath) from None

    def ls(self, prefix: str = "") -> tuple[str, ...]:  # type: ignore[override]
        return tuple(p for p in self._files if p.startswith(prefix))  # type: ignore[attr-defined]


def test_python_metrics_including_loop_built_names() -> None:
    src = """
class Stats:
    def __init__(self, labelnames):
        base = ["model_name", "engine"]
        self.a = self._gauge_cls(name="vllm:running", documentation="Running requests.", labelnames=base)
        self.b = self._counter_cls(name="vllm:done", documentation="Done.", labelnames=base + ["reason"])
        specs = [("vllm:drafts", "Number of drafts."), ("vllm:accepted", "Accepted tokens.")]
        self.c = [self._counter_cls(name=name, documentation=doc, labelnames=labelnames) for name, doc in specs]
        self.d = Histogram(name="other:ignored", documentation="x")
"""
    metrics, used = vllm_extract.extract_python_metrics(
        _FakeRepo({"m.py": src}), ["m.py"], ["model_name", "engine"], ("vllm:",)
    )
    got = {m["name"]: (m["type"], m["labels"]) for m in metrics}
    assert got == {
        "vllm:running": ("gauge", ["model_name", "engine"]),
        "vllm:done": ("counter", ["model_name", "engine", "reason"]),
        "vllm:drafts": ("counter", ["model_name", "engine"]),
        "vllm:accepted": ("counter", ["model_name", "engine"]),
    }
    assert used == ["m.py"]


# -- llama.cpp --------------------------------------------------------------

ARG_CPP = r"""
    if (ex == LLAMA_EXAMPLE_SERVER) {
        params.n_parallel = -1;
    }
    add_opt(common_arg(
        {"-c", "--ctx-size"}, "N",
        string_format("size of the prompt context (default: %d, 0 = loaded from model)", params.n_ctx),
        [](common_params & params, int value) {
            params.n_ctx = value; // a ) tricky " comment
        }
    ).set_env("LLAMA_ARG_CTX_SIZE"));
    add_opt(common_arg(
        {"--jinja"},
        {"--no-jinja"},
        string_format("whether to use jinja (default: %s)", params.use_jinja ? "enabled" : "disabled"),
        [](common_params & params, bool value) { params.use_jinja = value; }
    ).set_examples({LLAMA_EXAMPLE_SERVER, LLAMA_EXAMPLE_CLI}).set_env("LLAMA_ARG_JINJA"));
    add_opt(common_arg(
        {"-np", "--parallel"}, "N",
        string_format("number of server slots (default: %d, -1 = auto)", params.n_parallel),
        [](common_params & params, int value) { params.n_parallel = value; }
    ).set_examples({LLAMA_EXAMPLE_SERVER}));
    add_opt(common_arg(
        {"--temp"}, "N",
        string_format("temperature (default: %.2f)", (double)params.sampling.temp),
        [](common_params & params, const std::string & value) { params.sampling.temp = std::stof(value); }
    ).set_sparam());
    add_opt(common_arg(
        {"-sm", "--split-mode"}, "{none,layer,row}",
        "how to split the model, one of:\n"
        "- none: use one GPU only",
        [](common_params & params, const std::string & value) {}
    ));
    add_opt(common_arg(
        {"--tts-only"},
        "only for the tts example",
        [](common_params & params) {}
    ).set_examples({LLAMA_EXAMPLE_TTS}));
    add_opt(common_arg(
        {"--dyn"}, "N",
        string_format("depends on runtime (default: %s)", runtime_name(params.x).c_str()),
        [](common_params & params, const std::string & value) {}
    ));
"""
COMMON_H = """
struct common_params_sampling {
    float temp = 0.80f; // temperature
};
struct common_params {
    int32_t n_ctx = 4096; // context size
    int32_t n_parallel = 1;
    bool use_jinja = true;
    struct common_params_sampling sampling;
};
"""


def test_llamacpp_options() -> None:
    structs = llamacpp.parse_structs(COMMON_H)
    evaluator = llamacpp.Evaluator(structs, llamacpp.server_overrides(ARG_CPP))
    options = {o["name"]: o for o in llamacpp.parse_options(ARG_CPP, evaluator)}
    ctx = options["--ctx-size"]
    assert (ctx["aliases"], ctx["type"], ctx["default"], ctx["env"]) == (["-c"], "int", 4096, "LLAMA_ARG_CTX_SIZE")
    assert ctx["help"] == "size of the prompt context (default: 4096, 0 = loaded from model)"
    jinja = options["--jinja"]
    assert (jinja["negations"], jinja["default"], jinja["type"]) == (["--no-jinja"], True, "bool")
    assert options["--parallel"]["default"] == -1  # the server-specific override wins
    assert options["--temp"]["default"] == 0.8
    assert options["--temp"]["group"] == "sampling"
    assert options["--split-mode"]["choices"] == ["none", "layer", "row"]
    assert llamacpp.applies_to_server(options["--ctx-size"])
    assert not llamacpp.applies_to_server(options["--tts-only"])
    assert options["--dyn"]["help_partial"]
    assert "default" not in options["--dyn"]


def test_llamacpp_metrics_all_styles() -> None:
    old = (
        '{"counter", {{ {"name", "prompt_tokens_total"}, {"help", "Number of prompt tokens processed."}, {"value", 1} }}},'
        '{"gauge", {{ {"name", "requests_processing"}, {"help", "Number of requests processing."}, {"value", 2} }}}'
    )
    new = """const std::vector<metric_item> counters = { { "n_decode_total", "Total number of "
        "llama_decode() calls", (double) x }, };
        const std::vector<metric_item> gauges = { { "requests_deferred", "Number of requests deferred", (double) y } };
        prometheus << "# HELP llamacpp:per_pos_total" " Accepted tokens per draft position\\n"
                   << "# TYPE llamacpp:per_pos_total counter\\n";
        prometheus << "llamacpp:per_pos_total{position=\\"" << i;"""
    got = {m["name"]: (m["type"], m["labels"]) for m in llamacpp.parse_metrics(old, "s.cpp")}
    assert got == {"llamacpp:prompt_tokens_total": ("counter", []), "llamacpp:requests_processing": ("gauge", [])}
    got = {m["name"]: (m["type"], m["labels"], m["help"]) for m in llamacpp.parse_metrics(new, "s.cpp")}
    assert got == {
        "llamacpp:n_decode_total": ("counter", [], "Total number of llama_decode() calls"),
        "llamacpp:requests_deferred": ("gauge", [], "Number of requests deferred"),
        "llamacpp:per_pos_total": ("counter", ["position"], "Accepted tokens per draft position"),
    }


# -- TGI --------------------------------------------------------------------

MAIN_RS = """
#[derive(Clone, Copy, Debug, ValueEnum, Deserialize)]
#[serde(rename_all = "kebab-case")]
enum Quantization {
    /// doc
    Awq,
    BitsandbytesNf4,
    #[clap(name = "fp8_e4m3fn")]
    Fp8,
}

struct Args {
    /// The name of the model to load.
    /// Second line.
    #[clap(default_value = "bigscience/bloom-560m", long, env)]
    model_id: String,

    #[clap(long, env, value_enum)]
    quantize: Option<Quantization>,

    /// Whether to shard.
    #[clap(long, env)]
    sharded: Option<bool>,

    #[clap(
        default_value = "128",
        long,
        env
    )]
    max_concurrent_requests: usize,

    #[clap(long, env, value_enum)]
    trust_remote_code: bool,

    #[clap(default_value = "3000", long, short, env)]
    port: u16,

    #[clap(long, env = "HF_HUB_CACHE")]
    cache: Option<String>,
}
"""


def test_tgi_clap_struct() -> None:
    options = {o["name"]: o for o in tgi.parse_args_struct(MAIN_RS, "main.rs")}
    assert list(options) == [
        "--model-id",
        "--quantize",
        "--sharded",
        "--max-concurrent-requests",
        "--trust-remote-code",
        "--port",
        "--cache",
    ]
    model = options["--model-id"]
    assert (model["default"], model["env"], model["help"]) == (
        "bigscience/bloom-560m",
        "MODEL_ID",
        "The name of the model to load. Second line.",
    )
    assert options["--quantize"]["choices"] == ["awq", "bitsandbytes-nf4", "fp8_e4m3fn"]
    assert options["--sharded"]["choices"] == ["true", "false"]
    assert (options["--max-concurrent-requests"]["type"], options["--max-concurrent-requests"]["default"]) == (
        "int",
        128,
    )
    assert (options["--trust-remote-code"]["type"], options["--trust-remote-code"]["default"]) == ("bool", False)
    assert options["--cache"]["env"] == "HF_HUB_CACHE"


def test_tgi_metrics_macros() -> None:
    src = """
    metrics::counter!("tgi_request_failure", "err" => "validation").increment(1);
    metrics::counter!("tgi_request_failure", "err" => "overloaded").increment(1);
    metrics::histogram!("tgi_request_duration").record(1.0);
    metrics::describe_histogram!(
        "tgi_request_duration",
        metrics::Unit::Seconds,
        "Total time spent processing the request"
    );
    metrics::describe_gauge!("tgi_queue_size", metrics::Unit::Count, "Current queue size");
    """
    found: dict[str, dict[str, object]] = {}
    assert tgi.parse_metrics(src, "server.rs", found)
    assert found["tgi_request_failure"]["labels"] == ["err"]
    assert found["tgi_request_duration"]["help"] == "Total time spent processing the request"
    assert found["tgi_queue_size"]["type"] == "gauge"


# -- Ollama -----------------------------------------------------------------


def test_ollama_sources() -> None:
    config = """
        "OLLAMA_KEEP_ALIVE":   {"OLLAMA_KEEP_ALIVE", KeepAlive(), "The duration that models stay loaded in memory (default \\"5m\\")"},
        "OLLAMA_NOPRUNE":      {"OLLAMA_NOPRUNE", NoPrune(), "Do not prune model blobs on startup"},
        ret["OLLAMA_VULKAN"] = EnvVar{"OLLAMA_VULKAN", EnableVulkan(true), "Enable Vulkan support"}
    """
    env = {o["name"]: o for o in ollama.parse_env(config)}
    assert env["OLLAMA_KEEP_ALIVE"]["default"] == "5m"
    assert set(env) == {"OLLAMA_KEEP_ALIVE", "OLLAMA_NOPRUNE", "OLLAMA_VULKAN"}
    types = """
type Options struct {
	Runner

	Seed             int      `json:"seed,omitempty"`
	TypicalP         float32  `json:"typical_p,omitempty"` // Deprecated: rejected
	Stop             []string `json:"stop,omitempty"`
}
type Runner struct {
	NumCtx          int   `json:"num_ctx,omitempty"`
	UseMMap         *bool `json:"use_mmap,omitempty"`
}
func DefaultOptions() Options {
	return Options{
		Seed:             -1,
		TypicalP:         1.0,
		Runner: Runner{
			NumCtx:          int(envconfig.ContextLength()),
		},
	}
}
"""
    params = {o["name"]: o for o in ollama.parse_params(types)}
    assert params["seed"]["default"] == -1
    assert params["typical_p"]["deprecated"]
    assert params["stop"]["type"] == "list[str]"
    assert (params["num_ctx"]["scope"], "default" in params["num_ctx"]) == ("load", False)
    assert params["use_mmap"]["type"] == "bool"
    parser = 'func isValidCommand(cmd string) bool {\n\tswitch strings.ToLower(cmd) {\n\tcase "from", "parameter":\n\t\treturn true\n\tdefault:\n\t\treturn false\n\t}\n}'
    assert [o["name"] for o in ollama.parse_instructions(parser)] == ["FROM", "PARAMETER"]
