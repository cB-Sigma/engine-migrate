"""Property: translating A -> B -> A never invents an option."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from engine_migrate import db, mapping, shapes, translate

PAIRS = [
    ("vllm", "sglang"),
    ("sglang", "vllm"),
    ("vllm", "llamacpp"),
    ("llamacpp", "vllm"),
    ("sglang", "llamacpp"),
    ("llamacpp", "sglang"),
    ("vllm", "tgi"),
    ("tgi", "vllm"),
    ("sglang", "tgi"),
    ("vllm", "ollama"),
]

_VALUES = st.sampled_from(
    ["1", "2", "0.5", "auto", "org/model", "/models/a.gguf", "name=/path", "fp8", "true", "0.0.0.0"]
)


def _mapped_options(engine: str) -> list[db.Option]:
    ev = db.load(engine)
    out = []
    for option in ev.options_of("cli"):
        if mapping.find_binding(ev, option) is not None:
            out.append(option)
    return out


def _canonical(ev: db.EngineVersion, settings_: list[shapes.Setting]) -> set[str]:
    names = set()
    for setting in settings_:
        option = ev.find(setting.key)
        assert option is not None, f"{setting.key} is not an option of {ev.label}"
        names.add(option.name)
    return names


@st.composite
def command_lines(draw: st.DrawFn, engine: str) -> list[shapes.Setting]:
    options = _mapped_options(engine)
    chosen = draw(st.lists(st.sampled_from(options), min_size=1, max_size=8, unique_by=lambda o: o.name))
    out = []
    for option in chosen:
        spelling = draw(st.sampled_from(option.spellings))
        if option.type == "bool":
            out.append(shapes.Setting(spelling, [], "arg"))
        elif option.is_list:
            out.append(shapes.Setting(spelling, draw(st.lists(_VALUES, min_size=1, max_size=3)), "arg"))
        else:
            out.append(shapes.Setting(spelling, [draw(_VALUES)], "arg"))
    return out


@settings(max_examples=60, deadline=None)
@given(data=st.data(), pair=st.sampled_from(PAIRS))
def test_round_trip_does_not_invent_options(data: st.DataObject, pair: tuple[str, str]) -> None:
    a, b = db.load(pair[0]), db.load(pair[1])
    original = data.draw(command_lines(pair[0]))
    parsed = shapes.ParsedConfig("command", a.engine, original)
    _, forward, _ = translate.translate_settings(parsed, a, b)
    there = shapes.ParsedConfig("command", b.engine, forward)
    shapes.normalise_env(there, b)
    _, back, _ = translate.translate_settings(there, b, a)
    before = _canonical(a, original)
    after = _canonical(a, back)
    concepts_before = {mapping.find_binding(a, a.find(n))[0].id for n in before}  # type: ignore[arg-type,index]
    concepts_after = {mapping.find_binding(a, a.find(n))[0].id for n in after}  # type: ignore[arg-type,index]
    # Nothing comes back that was not asked for: no new concept ...
    assert concepts_after <= concepts_before
    # ... and every forward-translated option exists in the target database.
    _canonical(b, forward)


@settings(max_examples=40, deadline=None)
@given(data=st.data(), pair=st.sampled_from(PAIRS[:6]))
def test_round_trip_through_text_is_stable(data: st.DataObject, pair: tuple[str, str]) -> None:
    """Emit as a command line, parse it again, and get the same options."""
    a, b = db.load(pair[0]), db.load(pair[1])
    original = data.draw(command_lines(pair[0]))
    parsed = shapes.ParsedConfig("command", a.engine, original)
    _, forward, _ = translate.translate_settings(parsed, a, b)
    text, _ = shapes.emit(parsed, forward, b, a)
    if not forward:
        return
    reparsed = shapes.parse(text, b, "command")
    assert _canonical(
        b, [s for s in reparsed.settings if s.origin != "positional" or s.key.startswith("-")]
    ) == _canonical(b, forward)
