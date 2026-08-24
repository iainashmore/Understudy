"""Hex colour parsing, shared by the layers.

Colour format is a convention of the task set -- every prompt gives #rrggbb --
not a feature of any one layer, so all layers read it the same way. Each layer
still writes its own error message, in its own register.
"""

from __future__ import annotations

import re
from typing import Any

_HEX6 = re.compile(r"^#[0-9a-fA-F]{6}$")
_HEX3 = re.compile(r"^#[0-9a-fA-F]{3}$")


def normalise_hex(value: Any) -> str | None:
    """Return a lowercase #rrggbb string, or None if this is not a hex colour.

    Colour *names* are deliberately not accepted anywhere. Several in the task
    set do not mean what a reader expects -- the prompts' "brown" is saddlebrown
    and their "dark red" is firebrick -- so a name would quietly draw the wrong
    colour instead of producing an error the agent can act on.
    """
    if not isinstance(value, str):
        return None
    if _HEX6.match(value):
        return value.lower()
    if _HEX3.match(value):
        return "#" + "".join(character * 2 for character in value[1:]).lower()
    return None


def to_rgb(hex_colour: str) -> tuple[int, int, int]:
    return tuple(int(hex_colour[index : index + 2], 16) for index in (1, 3, 5))
