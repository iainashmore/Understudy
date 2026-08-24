"""Environment implementations, one per abstraction layer.

The registry is what lets the runner sweep whatever layers currently exist
without knowing which those are.
"""

from collections.abc import Callable, Sequence
from typing import Any

from harness.environment import Environment
from harness.environments.api import APIEnvironment, oracle_actions
from harness.interaction import Action, Layer

#: Layers with a working implementation. UI and Kernel join this as they land.
ENVIRONMENTS: dict[Layer, Callable[[], Environment]] = {
    Layer.API: APIEnvironment,
}

#: How to say a golden recipe in each layer's action space, for the oracle
#: diagnostic. Only ever used to prove an environment can pass at all.
ORACLE_TRANSLATORS: dict[Layer, Callable[[Sequence[dict[str, Any]]], list[Action]]] = {
    Layer.API: oracle_actions,
}


def available_layers() -> list[Layer]:
    return [layer for layer in Layer if layer in ENVIRONMENTS]


def build(layer: Layer) -> Environment:
    if layer not in ENVIRONMENTS:
        raise KeyError(
            f"no environment for the {layer.value} layer yet; "
            f"available: {[l.value for l in available_layers()]}"
        )
    return ENVIRONMENTS[layer]()


__all__ = [
    "APIEnvironment",
    "ENVIRONMENTS",
    "ORACLE_TRANSLATORS",
    "available_layers",
    "build",
    "oracle_actions",
]
