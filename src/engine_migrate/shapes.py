"""Parse and re-emit serving configurations in the shape they came in.

Supported shapes:

``command``     a shell command line (``vllm serve ...``)
``docker``      a ``docker run`` / ``podman run`` line
``kubernetes``  a manifest (or bare container spec) with ``args`` / ``env``
``yaml``        a flat YAML config file whose keys are flag names
``env``         ``KEY=value`` lines (plus Modelfile lines for Ollama)
"""

from __future__ import annotations

import copy
import re
import shlex
from dataclasses import dataclass, field
from typing import Any

import yaml

from . import db
from .errors import ConfigParseError

SHAPES = ("command", "docker", "kubernetes", "yaml", "env")

LAUNCHERS: dict[str, list[str]] = {
    "vllm": ["vllm", "serve"],
    "sglang": ["python3", "-m", "sglang.launch_server"],
    "llamacpp": ["llama-server"],
    "tgi": ["text-generation-launcher"],
    "ollama": ["ollama", "serve"],
}

# ``docker run`` options that consume the next token.
_DOCKER_VALUE_OPTS = {
    "-p", "--publish", "-v", "--volume", "-e", "--env", "--env-file", "--name", "--gpus",
    "--runtime", "--shm-size", "--ipc", "--network", "--net", "-u", "--user", "-w", "--workdir",
    "--entrypoint", "--restart", "-m", "--memory", "--cpus", "--device", "--mount", "-l",
    "--label", "-h", "--hostname", "--add-host", "--ulimit", "--platform", "--pull", "--cap-add",
    "--cap-drop", "--security-opt", "--group-add", "--log-driver", "--log-opt", "--pid",
    "--memory-swap", "--cpuset-cpus", "--health-cmd", "--stop-timeout", "--tmpfs", "--dns",
}  # fmt: skip

_ENV_LINE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_NEGATIVE_NUMBER = re.compile(r"^-\d")
_TRUE = {"1", "true", "yes", "on", "enabled"}
_FALSE = {"0", "false", "no", "off", "disabled"}


@dataclass
class Setting:
    """One option as written in the input."""

    key: str
    values: list[str] = field(default_factory=list)
    origin: str = "arg"  # arg | positional | env | yaml | modelfile | param
    enabled: bool | None = None  # explicit boolean from YAML / env, when given
    written: str | None = None  # original spelling when ``key`` was normalised

    @property
    def display(self) -> str:
        key = self.written or self.key
        if self.origin == "positional":
            return f"{' '.join(self.values)} (positional)"
        if self.origin == "env":
            return f"{key}={' '.join(self.values)}"
        if self.origin == "yaml":
            if self.enabled is not None and not self.values:
                return f"{key.lstrip('-')}: {str(self.enabled).lower()}"
            return f"{key.lstrip('-')}: {' '.join(self.values)}"
        if self.origin in {"modelfile", "param"}:
            return f"{key} {' '.join(self.values)}".strip()
        return " ".join([key, *self.values])


@dataclass
class ParsedConfig:
    shape: str
    engine: str
    settings: list[Setting]
    prefix: list[str] = field(default_factory=list)
    envelope: Any = None


def is_truthy(text: str) -> bool | None:
    low = text.strip().lower()
    if low in _TRUE:
        return True
    if low in _FALSE:
        return False
    return None


def detect_shape(text: str) -> str:
    """Best-effort guess of the input shape."""
    stripped = _join_continuations(text).strip()
    if not stripped:
        raise ConfigParseError("the configuration is empty")
    first = stripped.split(None, 2)
    if len(first) >= 2 and first[0] in {"docker", "podman", "nerdctl"} and first[1] == "run":
        return "docker"
    if first[0] == "sudo" and len(first) > 2 and first[2].startswith("run"):
        return "docker"
    lines = [ln for ln in stripped.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    env_like = bool(lines) and all(_ENV_LINE.match(ln.strip()) or _is_modelfile_line(ln) for ln in lines)
    if env_like and (len(lines) > 1 or not any(tok.startswith("-") for tok in lines[0].split()[1:])):
        return "env"
    try:
        docs = [d for d in yaml.safe_load_all(text) if d is not None]
    except yaml.YAMLError:
        docs = []
    if docs and all(isinstance(d, dict) for d in docs):
        if any(_find_containers(d) for d in docs):
            return "kubernetes"
        if len(docs) == 1 and len(lines) >= 1 and all(":" in ln for ln in lines if not ln.startswith((" ", "-"))):
            return "yaml"
    return "command"


def _is_modelfile_line(line: str) -> bool:
    head = line.strip().split(None, 1)[0].upper() if line.strip() else ""
    return head in {"FROM", "PARAMETER", "TEMPLATE", "SYSTEM", "ADAPTER", "LICENSE", "MESSAGE"}


def _join_continuations(text: str) -> str:
    return re.sub(r"\\\r?\n", " ", text)


def _split(text: str) -> list[str]:
    try:
        return shlex.split(_join_continuations(text), comments=True, posix=True)
    except ValueError as exc:
        raise ConfigParseError(f"cannot tokenise the command line: {exc}") from exc


# ---------------------------------------------------------------------------
# Argument vectors
# ---------------------------------------------------------------------------


def parse_argv(tokens: list[str], ev: db.EngineVersion) -> tuple[list[str], list[Setting]]:
    """Split ``tokens`` into the launcher prefix and settings."""
    prefix: list[str] = []
    settings: list[Setting] = []
    i = 0
    # Leading VAR=value assignments and the launcher itself.
    while i < len(tokens):
        tok = tokens[i]
        env = _ENV_LINE.match(tok)
        if env and not prefix:
            settings.append(Setting(env.group(1), [env.group(2)], "env"))
            i += 1
            continue
        if tok.startswith("-") and not (prefix and tok in {"-m", "-u"} and _is_python(prefix[-1])):
            break
        prefix.append(tok)
        i += 1
        if tok in {"-m"} and i < len(tokens):
            prefix.append(tokens[i])
            i += 1
    # vLLM: `vllm serve <model>` takes the model as a positional argument.
    if ev.engine == "vllm" and "serve" in prefix:
        idx = prefix.index("serve")
        extra = prefix[idx + 1 :]
        if extra:
            settings.append(Setting("--model", [extra[0]], "positional"))
            prefix = prefix[: idx + 1]
    while i < len(tokens):
        tok = tokens[i]
        i += 1
        if not tok.startswith("-") or _NEGATIVE_NUMBER.match(tok):
            if ev.engine == "vllm" and not any(s.key in {"--model", "-m"} for s in settings):
                settings.append(Setting("--model", [tok], "positional"))
            else:
                settings.append(Setting(tok, [], "positional"))
            continue
        key, eq, inline = tok.partition("=")
        option = ev.find(key)
        values: list[str] = []
        if eq:
            values = [inline]
        elif option is None:
            if i < len(tokens) and _is_value(tokens[i]):
                values = [tokens[i]]
                i += 1
        elif option.is_list:
            while i < len(tokens) and _is_value(tokens[i]):
                values.append(tokens[i])
                i += 1
        elif option.takes_value and key not in option.negations and i < len(tokens) and _is_value(tokens[i]):
            values = [tokens[i]]
            i += 1
        settings.append(Setting(key, values, "arg"))
    return prefix, settings


def _is_python(token: str) -> bool:
    return bool(re.search(r"(^|/)python[\d.]*$", token))


def _is_value(token: str) -> bool:
    return not token.startswith("-") or bool(_NEGATIVE_NUMBER.match(token)) or token == "-"


def emit_argv(settings: list[Setting]) -> list[list[str]]:
    """Groups of tokens, one group per option."""
    groups: list[list[str]] = []
    for setting in settings:
        if setting.origin in {"env", "modelfile", "param"}:
            continue
        if setting.origin == "positional":
            groups.append(list(setting.values) or [setting.key])
        else:
            groups.append([setting.key, *setting.values])
    return groups


_SHELL_SAFE = re.compile(r"[\w@%+=:,./~$-]+")


def quote(token: str) -> str:
    """Quote for display, keeping ``$VAR`` and ``~`` expandable as written."""
    if _SHELL_SAFE.fullmatch(token):
        return token
    if "$" in token or "'" in token:
        return '"' + token.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`") + '"'
    return "'" + token + "'"


def _join(tokens: list[str]) -> str:
    return " ".join(quote(t) for t in tokens)


def _format_command(head: list[str], groups: list[list[str]]) -> str:
    parts = [_join(head)] if head else []
    parts += [_join(group) for group in groups]
    return " \\\n  ".join(parts)


# ---------------------------------------------------------------------------
# Shape: command
# ---------------------------------------------------------------------------


def parse_command(text: str, ev: db.EngineVersion) -> ParsedConfig:
    prefix, settings = parse_argv(_split(text), ev)
    return ParsedConfig("command", ev.engine, settings, prefix)


def emit_command(engine: str, settings: list[Setting]) -> str:
    env = [f"{s.key}={quote(' '.join(s.values))}" for s in settings if s.origin == "env"]
    head = list(LAUNCHERS[engine])
    positional = [s for s in settings if s.origin == "positional"]
    rest = [s for s in settings if s.origin == "arg"]
    for setting in positional:
        head += setting.values
    text = _format_command(head, emit_argv(rest))
    return (" ".join(env) + " " + text) if env else text


# ---------------------------------------------------------------------------
# Shape: docker
# ---------------------------------------------------------------------------


def parse_docker(text: str, ev: db.EngineVersion) -> ParsedConfig:
    tokens = _split(text)
    try:
        run_idx = tokens.index("run")
    except ValueError as exc:
        raise ConfigParseError("not a `docker run` line") from exc
    head = tokens[: run_idx + 1]
    docker_opts: list[list[str]] = []
    env_settings: list[Setting] = []
    i = run_idx + 1
    image: str | None = None
    while i < len(tokens):
        tok = tokens[i]
        if not tok.startswith("-"):
            image = tok
            i += 1
            break
        name, eq, inline = tok.partition("=")
        if eq and name.startswith("--"):
            group = [tok]
            value = inline
            i += 1
        elif name in _DOCKER_VALUE_OPTS and i + 1 < len(tokens):
            group = [tok, tokens[i + 1]]
            value = tokens[i + 1]
            i += 2
        else:
            group = [tok]
            value = ""
            i += 1
        if name in {"-e", "--env"}:
            env = _ENV_LINE.match(value)
            if env and _env_option(ev, env.group(1)) is not None:
                env_settings.append(Setting(env.group(1), [env.group(2)], "env"))
                continue
        docker_opts.append(group)
    if image is None:
        raise ConfigParseError("no image found in the `docker run` line")
    prefix, settings = parse_argv(tokens[i:], ev)
    envelope = {"head": head, "options": docker_opts, "image": image}
    return ParsedConfig("docker", ev.engine, env_settings + settings, prefix, envelope)


def emit_docker(engine: str, settings: list[Setting], envelope: dict[str, Any]) -> str:
    groups: list[list[str]] = [list(g) for g in envelope["options"]]
    for setting in settings:
        if setting.origin == "env":
            groups.append(["-e", f"{setting.key}={' '.join(setting.values)}"])
    groups.append([image_placeholder(engine)])
    head = list(LAUNCHERS[engine])
    for setting in settings:
        if setting.origin == "positional":
            head += setting.values
    groups.append(head)
    groups += emit_argv([s for s in settings if s.origin == "arg"])
    return _format_command(list(envelope["head"]), groups)


def image_placeholder(engine: str) -> str:
    return f"<{engine}-image>"


# ---------------------------------------------------------------------------
# Shape: kubernetes
# ---------------------------------------------------------------------------


def _find_containers(node: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if isinstance(node.get("image"), str) and any(k in node for k in ("args", "command", "env")):
            found.append(node)
        for key, value in node.items():
            if key in {"containers", "initContainers"} and isinstance(value, list):
                found += [c for c in value if isinstance(c, dict) and key == "containers"]
            else:
                found += _find_containers(value)
    elif isinstance(node, list):
        for item in node:
            found += _find_containers(item)
    unique: list[dict[str, Any]] = []
    for item in found:
        if not any(item is u for u in unique):
            unique.append(item)
    return unique


def _pick_container(containers: list[dict[str, Any]], ev: db.EngineVersion) -> dict[str, Any]:
    launcher = LAUNCHERS[ev.engine]
    needles = {ev.engine, launcher[0], launcher[-1], db.ENGINE_TITLES[ev.engine].lower()}
    for container in containers:
        blob = " ".join(
            [str(container.get("image", "")), str(container.get("name", "")), *map(str, container.get("command") or [])]
        ).lower()
        if any(n.lower() in blob for n in needles):
            return container
    for container in containers:
        if any(str(a).startswith("-") and ev.find(str(a).split("=")[0]) for a in container.get("args") or []):
            return container
    return containers[0]


def parse_kubernetes(text: str, ev: db.EngineVersion) -> ParsedConfig:
    try:
        docs = list(yaml.safe_load_all(text))
    except yaml.YAMLError as exc:
        raise ConfigParseError(f"invalid YAML: {exc}") from exc
    containers = [c for d in docs for c in _find_containers(d)]
    if not containers:
        raise ConfigParseError("no container with args/command/env found in the manifest")
    container = _pick_container(containers, ev)
    command = [str(t) for t in container.get("command") or []]
    args = [str(t) for t in container.get("args") or []]
    if len(command) >= 3 and command[-2] == "-c" and re.search(r"(^|/)(ba|z)?sh$", command[0]):
        tokens = _split(command[-1])  # sh -c "<command line>"
    else:
        tokens = command + args
    prefix, settings = parse_argv(tokens, ev)
    env_settings: list[Setting] = []
    for entry in container.get("env") or []:
        if not isinstance(entry, dict) or "value" not in entry:
            continue
        if _env_option(ev, str(entry.get("name"))) is not None:
            env_settings.append(Setting(str(entry["name"]), [str(entry["value"])], "env"))
    envelope = {"docs": docs, "index": containers.index(container)}
    return ParsedConfig("kubernetes", ev.engine, env_settings + settings, prefix, envelope)


def emit_kubernetes(engine: str, settings: list[Setting], envelope: dict[str, Any], src: db.EngineVersion) -> str:
    docs = copy.deepcopy(envelope["docs"])
    containers = [c for d in docs for c in _find_containers(d)]
    container = containers[envelope["index"]]
    head = list(LAUNCHERS[engine])
    for setting in settings:
        if setting.origin == "positional":
            head += setting.values
    args: list[str] = []
    for group in emit_argv([s for s in settings if s.origin == "arg"]):
        args += group
    container["image"] = image_placeholder(engine)
    rebuilt: dict[str, Any] = {}
    for key, value in container.items():
        if key in {"command", "args"}:
            continue
        rebuilt[key] = value
        if key == "image":
            rebuilt["command"] = head
            rebuilt["args"] = args
    kept_env = [
        e
        for e in container.get("env") or []
        if not (isinstance(e, dict) and "value" in e and _env_option(src, str(e.get("name"))) is not None)
    ]
    new_env = [{"name": s.key, "value": " ".join(s.values)} for s in settings if s.origin == "env"]
    if kept_env or new_env:
        rebuilt["env"] = kept_env + new_env
    else:
        rebuilt.pop("env", None)
    container.clear()
    container.update(rebuilt)
    return yaml.safe_dump_all(docs, sort_keys=False, default_flow_style=False, width=1000).rstrip("\n")


# ---------------------------------------------------------------------------
# Shape: yaml config file
# ---------------------------------------------------------------------------


def parse_yaml(text: str, ev: db.EngineVersion) -> ParsedConfig:
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigParseError(f"invalid YAML: {exc}") from exc
    if not isinstance(doc, dict):
        raise ConfigParseError("a YAML config file must be a mapping of option names to values")
    settings: list[Setting] = []
    for raw_key, value in doc.items():
        written = str(raw_key)
        key = "--" + written.lstrip("-").replace("_", "-")
        if ev.find(key) is None and ev.find("-" + written.lstrip("-")) is not None:
            key = "-" + written.lstrip("-")
        if ev.engine == "vllm" and written in {"model", "model_tag", "model-tag"}:
            key = "--model"
        setting = Setting(key, [], "yaml", written=written)
        if isinstance(value, bool):
            setting.enabled = value
        elif value is None:
            setting.enabled = True
        elif isinstance(value, list):
            setting.values = [_scalar(v) for v in value]
        else:
            setting.values = [_scalar(value)]
        settings.append(setting)
    return ParsedConfig("yaml", ev.engine, settings)


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (dict, list)):
        import json

        return json.dumps(value)
    return str(value)


def _typed(text: str) -> Any:
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    return text


def emit_yaml(settings: list[Setting], dst: db.EngineVersion) -> str:
    doc: dict[str, Any] = {}
    for setting in settings:
        if setting.origin in {"env", "modelfile", "param"}:
            continue
        key = setting.key.lstrip("-")
        option = dst.find(setting.key)
        if setting.origin == "positional":
            key = "model"
        if not setting.values:
            doc[key] = True if setting.enabled is None else setting.enabled
        elif option is not None and option.is_list:
            doc[key] = [_typed(v) for v in setting.values]
        else:
            doc[key] = _typed(setting.values[0]) if len(setting.values) == 1 else [_typed(v) for v in setting.values]
    return yaml.safe_dump(doc, sort_keys=False, default_flow_style=False, width=1000).rstrip("\n")


# ---------------------------------------------------------------------------
# Shape: env file (and Ollama Modelfile lines)
# ---------------------------------------------------------------------------


def _env_option(ev: db.EngineVersion, name: str) -> db.Option | None:
    option = ev.find(name)
    return option if option is not None and option.kind == "env" else None


def parse_env(text: str, ev: db.EngineVersion) -> ParsedConfig:
    settings: list[Setting] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        env = _ENV_LINE.match(line)
        if env:
            value = env.group(2).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            settings.append(Setting(env.group(1), [value], "env"))
            continue
        if _is_modelfile_line(line):
            head, _, rest = line.partition(" ")
            if head.upper() == "PARAMETER":
                name, _, value = rest.strip().partition(" ")
                settings.append(Setting(name, [value.strip()], "param"))
            else:
                settings.append(Setting(head.upper(), [rest.strip()], "modelfile"))
            continue
        raise ConfigParseError(f"cannot read line as KEY=value: {raw!r}")
    return ParsedConfig("env", ev.engine, settings)


def emit_env(settings: list[Setting]) -> str:
    env = [f"{s.key}={' '.join(s.values)}" for s in settings if s.origin == "env"]
    modelfile = [f"{s.key} {' '.join(s.values)}" for s in settings if s.origin == "modelfile"]
    modelfile += [f"PARAMETER {s.key} {' '.join(s.values)}" for s in settings if s.origin == "param"]
    blocks = []
    if env:
        blocks.append("\n".join(env))
    if modelfile:
        blocks.append("# Modelfile\n" + "\n".join(modelfile))
    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------


def normalise_env(parsed: ParsedConfig, ev: db.EngineVersion) -> None:
    """Rewrite env settings that are the environment form of a flag."""
    for setting in parsed.settings:
        if setting.origin != "env":
            continue
        option = _env_option(ev, setting.key)
        flag = option.raw.get("flag") if option is not None else None
        if isinstance(flag, str):
            setting.written = setting.key
            setting.key = flag


def _to_env(setting: Setting, dst: db.EngineVersion, undo: bool = False) -> Setting:
    """Use the environment form of a flag where the target engine defines one."""
    if undo:
        if setting.origin == "env" and setting.written is not None:
            values = [] if setting.enabled is not None else setting.values
            return Setting(setting.written, values, "arg", setting.enabled)
        return setting
    if setting.origin != "arg":
        return setting
    option = dst.find(setting.key)
    if option is None or option.env is None:
        return setting
    if not setting.values:
        state = setting.key not in option.negations
        return Setting(option.env, ["true" if state else "false"], "env", state, written=setting.key)
    return Setting(option.env, [",".join(setting.values)], "env", written=setting.key)


def parse(text: str, ev: db.EngineVersion, shape: str | None = None) -> ParsedConfig:
    """Parse ``text`` as a configuration of engine version ``ev``."""
    shape = shape or detect_shape(text)
    if shape == "command":
        parsed = parse_command(text, ev)
    elif shape == "docker":
        parsed = parse_docker(text, ev)
    elif shape == "kubernetes":
        parsed = parse_kubernetes(text, ev)
    elif shape == "yaml":
        parsed = parse_yaml(text, ev)
    elif shape == "env":
        parsed = parse_env(text, ev)
    else:
        raise ConfigParseError(f"unknown shape {shape!r} (expected one of {', '.join(SHAPES)})")
    normalise_env(parsed, ev)
    return parsed


def emit(
    parsed: ParsedConfig,
    settings: list[Setting],
    dst: db.EngineVersion,
    src: db.EngineVersion,
) -> tuple[str, list[str]]:
    """Render ``settings`` for ``dst`` in the shape of ``parsed``.

    Returns the text and notes about anything the shape could not express.
    """
    notes: list[str] = []
    shape = parsed.shape
    if dst.engine == "ollama" and shape != "env":
        notes.append(
            "Ollama is configured through environment variables and a Modelfile, so the output uses that form."
        )
        shape = "env"
    if shape == "env":
        settings = [_to_env(s, dst) for s in settings]
        if any(s.origin in {"arg", "positional"} for s in settings):
            notes.append(
                f"{db.ENGINE_TITLES[dst.engine]} has no environment form for some of these options, "
                "so the output is a command line."
            )
            shape = "command"
            settings = [_to_env(s, dst, undo=True) for s in settings]
    if shape == "command":
        return emit_command(dst.engine, settings), notes
    if shape == "docker":
        notes.append(
            f"The container image is not translated: replace {image_placeholder(dst.engine)} "
            "and drop the launcher words if the image already defines them as its entrypoint."
        )
        return emit_docker(dst.engine, settings, parsed.envelope), notes
    if shape == "kubernetes":
        notes.append(
            f"The container image is not translated: replace {image_placeholder(dst.engine)}; "
            "probes, ports and resources are copied unchanged and may need the new port."
        )
        return emit_kubernetes(dst.engine, settings, parsed.envelope, src), notes
    if shape == "yaml":
        if dst.engine not in {"vllm", "sglang"}:
            notes.append(
                f"{db.ENGINE_TITLES[dst.engine]} does not read YAML option files; the keys below are its flag names."
            )
        return emit_yaml(settings, dst), notes
    return emit_env(settings), notes
