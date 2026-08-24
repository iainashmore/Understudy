"""The shared agent/environment vocabulary."""

from __future__ import annotations

import pytest

from harness.interaction import (
    DONE,
    Action,
    Interface,
    Layer,
    Observation,
    Operation,
    Parameter,
)


def test_all_three_layers_exist():
    assert {layer.value for layer in Layer} == {"ui", "api", "kernel"}


def test_done_is_recognised():
    assert Action.done().is_done
    assert not Action("draw_circle").is_done


def test_action_rejects_a_missing_name():
    with pytest.raises(ValueError, match="non-empty"):
        Action("")
    with pytest.raises(ValueError, match="non-empty"):
        Action("   ")


def test_action_rejects_malformed_args():
    with pytest.raises(TypeError, match="dict"):
        Action("draw", ["not", "a", "dict"])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="strings"):
        Action("draw", {1: "one"})  # type: ignore[dict-item]


def test_action_is_frozen():
    action = Action("draw_circle", {"r": 40})
    with pytest.raises(Exception):
        action.name = "other"  # type: ignore[misc]


def test_action_serialises_for_the_trace():
    action = Action("draw_circle", {"cx": 100, "cy": 100, "r": 40})
    assert action.as_dict() == {
        "name": "draw_circle",
        "args": {"cx": 100, "cy": 100, "r": 40},
    }


def test_action_str_is_readable():
    assert str(Action("draw_circle", {"r": 40})) == "draw_circle(r=40)"


def test_observation_flags():
    assert not Observation("fine").failed
    assert Observation(error="boom").failed
    assert not Observation("fine").has_image
    assert Observation(image=b"png").has_image


def test_observation_digest_is_stable_and_distinguishing():
    assert Observation(image=b"a").image_digest == Observation(image=b"a").image_digest
    assert Observation(image=b"a").image_digest != Observation(image=b"b").image_digest
    assert Observation().image_digest is None


def test_observation_never_inlines_the_image():
    # Traces are read by humans; a base64 PNG per turn makes that impossible.
    record = Observation(text="ok", image=b"x" * 5000).as_dict()
    assert record["image_bytes"] == 5000
    assert record["image_digest"]
    assert not any(isinstance(value, bytes) for value in record.values())


def test_operation_signature():
    operation = Operation(
        name="draw_circle",
        summary="Draw a filled circle.",
        parameters=(
            Parameter("cx", "number", "centre x"),
            Parameter("fill", "colour", "fill colour", required=False),
        ),
    )
    assert operation.signature() == "draw_circle(cx: number, fill: colour = null)"
    assert "centre x" in operation.describe()


def test_interface_rejects_duplicate_operations():
    op = Operation("draw", "one")
    with pytest.raises(ValueError, match="duplicate"):
        Interface(Layer.API, "preamble", (op, op))


def test_done_is_available_at_every_layer():
    for layer in Layer:
        interface = Interface(layer, "preamble")
        assert DONE in interface.operation_names
        assert interface.accepts(Action.done())


def test_interface_rejects_unknown_operations():
    interface = Interface(Layer.API, "p", (Operation("draw_circle", "s"),))
    assert interface.accepts(Action("draw_circle"))
    assert not interface.accepts(Action("draw_hexagon"))


def test_describe_covers_the_preamble_the_operations_and_done():
    interface = Interface(
        Layer.KERNEL,
        "You are writing directly into a pixel buffer.",
        (Operation("set_pixel", "Set one pixel.", (Parameter("x", "integer", "column"),)),),
    )
    described = interface.describe()
    assert "pixel buffer" in described
    assert "set_pixel(x: integer)" in described
    assert "column" in described
    assert DONE in described
