"""Turn a demonstration into a flow.

Somebody does the thing once -- click, click, type a question, press Enter --
and this writes the flow that does it again, with the question swapped out.

Where the click path is recorded from matters. There is no accessibility tree
worth having in a CAD application and no DOM behind an embedded panel, so a
click is recorded as a picture: a crop of the window around the point that was
clicked, which the driver finds again by matching. That is the same mechanism
the hand-written blind flows use, so a recorded flow and a written one are the
same kind of thing and can be edited into each other.

What this module does not do is touch Windows. Events arrive already
normalised, screenshots arrive as arrays. The half that cannot run outside a
real desktop is the hook adapter in tools/record_flow.py, and it is kept as
thin as it can be so that everything decided here is decided in tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from understudy.vision import crop
from harness.image import to_png_bytes

#: How much of the window around a click becomes the anchor. Wide enough to
#: contain something with edges -- an icon, a label, a border -- because a
#: 20x20 crop of a flat panel matches everywhere. Tall enough to catch a line
#: of text above or below a featureless box.
ANCHOR = (160, 64)

#: Keys that are an instruction rather than text, in the SendKeys spelling the
#: driver types with.
NAMED_KEYS = {
    "return": "{ENTER}", "enter": "{ENTER}", "tab": "{TAB}",
    "escape": "{ESC}", "esc": "{ESC}", "back": "{BACKSPACE}",
    "backspace": "{BACKSPACE}", "delete": "{DELETE}", "space": " ",
    "up": "{UP}", "down": "{DOWN}", "left": "{LEFT}", "right": "{RIGHT}",
    "home": "{HOME}", "end": "{END}", "pgup": "{PGUP}", "pgdn": "{PGDN}",
}


@dataclass
class Anchor:
    """One picture, and where the click was relative to it."""

    name: str
    png: bytes
    dx: int
    dy: int
    width: int
    height: int
    #: Where the click landed in the window, so the point can be found again
    #: on the saved screen.
    point: tuple[int, int] = (0, 0)
    #: What was clicked, in words, when a model was asked. Empty otherwise,
    #: and the flow falls back to saying which click it was.
    described: str = ""
    #: The whole window at that moment. The crop is what the driver matches
    #: on; this is what lets anyone -- or anything -- work out afterwards what
    #: was actually clicked, and re-cut a different crop without asking for
    #: the recording to be done again.
    screen: bytes = b""


@dataclass
class Recorder:
    """Events in, a flow out."""

    anchor_size: tuple[int, int] = ANCHOR
    threshold: float = 0.88
    anchors: list[Anchor] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    _typed: list[str] = field(default_factory=list)
    _last_target: str = ""

    # -- events ---------------------------------------------------------------

    def click(self, x: int, y: int, image, origin: tuple[int, int] = (0, 0)) -> str:  # noqa: E501
        """A click at a screen point, with the window as it looked before it.

        The screenshot has to be the one taken *before* the click landed: half
        the point of an anchor is that it is what was on screen when the
        person decided to click there, and a menu that opened on the click is
        not that.
        """
        self._flush_text()
        window_x, window_y = x - origin[0], y - origin[1]
        anchor = self._cut(window_x, window_y, image, f"target_{len(self.anchors) + 1}")
        anchor.screen = to_png_bytes(image)
        anchor.point = (window_x, window_y)
        self.anchors.append(anchor)
        self._last_target = anchor.name
        self.steps.append({"action": "click", "target": anchor.name})
        return anchor.name

    def text(self, characters: str) -> None:
        """Printable characters. Accumulated: a person types a sentence, not 34
        separate keystrokes, and a flow with 34 type steps is unreadable."""
        if characters:
            self._typed.append(characters)

    def key(self, name: str) -> None:
        """A key that means something rather than says something."""
        spelled = NAMED_KEYS.get(name.lower())
        if spelled is None:
            return
        if spelled == " ":
            self.text(" ")
            return
        self._flush_text()
        self.steps.append({"action": "key", "keys": spelled})

    # -- the flow -------------------------------------------------------------

    def flow(self, name: str, title: str, app_config: dict[str, Any],
             read_region: dict[str, int] | None = None,
             description: str = "") -> dict[str, Any]:
        """The recorded path as a flow document.

        The longest thing typed becomes the prompt. That is the whole point of
        recording one: the click path is fixed and the question is what varies,
        so the sentence the person typed is lifted out into `prompts` and the
        step that typed it becomes a substitution.
        """
        self._flush_text()
        steps = [dict(step) for step in self.steps]
        prompt = self._lift_prompt(steps)

        document: dict[str, Any] = {
            "version": 1,
            "name": name,
            "title": title,
            "description": description or f"Recorded against {title}",
            "tags": ["recorded"],
            "target_app": {"native": app_config},
            "defaults": {"timeout_ms": 30000, "poll_interval_ms": 300,
                         "stable_for_ms": 2000},
            "targets": {
                anchor.name: {
                    "intent": anchor.described or f"recorded click {index + 1}",
                    "native": [{
                        "image": f"anchors/{name}/{anchor.name}.png",
                        "threshold": self.threshold,
                        **({"offset": {"dx": anchor.dx, "dy": anchor.dy}}
                           if (anchor.dx or anchor.dy) else {}),
                    }],
                }
                for index, anchor in enumerate(self.anchors)
            },
            "prompts": [{"id": "baseline", "prompt": prompt}],
            "steps": steps,
        }

        if read_region:
            # Without this the flow drives the application and records
            # nothing, which is a demo rather than a test.
            document["steps"].append({
                "action": "wait_for_stable", "target": self.anchors[-1].name,
                "mode": "pixels", "region": dict(read_region),
                "stable_for_ms": 4000, "timeout_ms": 180000,
            })
            document["steps"].append({
                "action": "read", "mode": "ocr",
                "region": dict(read_region), "store_as": "response",
            })
        return document

    def manifest(self) -> list[dict[str, Any]]:
        """What was done, where, and on what -- one entry per click.

        Kept next to the flow because it is the durable part of a recording:
        a screen, a point on it, and a description of what was there. A flow
        is one thing that can be built from that; it is not the only one.
        """
        return [
            {"target": anchor.name, "point": list(anchor.point),
             "screen": f"screens/{anchor.name}.png",
             "anchor": f"{anchor.name}.png",
             "clicked": anchor.described}
            for anchor in self.anchors
        ]

    def anchor_files(self) -> dict[str, bytes]:
        return {f"{anchor.name}.png": anchor.png for anchor in self.anchors}

    def screen_files(self) -> dict[str, bytes]:
        """The whole window at each interaction, kept beside the anchors.

        Not used at replay time. It is what makes a recording reviewable --
        which icon was that? -- and what a second pass would need to re-cut an
        anchor, or to name a target something better than target_3, without
        anybody going back to the workstation.
        """
        return {f"screens/{anchor.name}.png": anchor.screen
                for anchor in self.anchors if anchor.screen}

    # -- internals ------------------------------------------------------------

    def _cut(self, x: int, y: int, image, name: str) -> Anchor:
        """The window around a point, clamped to the window.

        Clamped rather than refused: a click near an edge is ordinary, and the
        offset carries the difference so the driver still acts on the point
        that was clicked rather than on the middle of the crop.
        """
        height, width = image.shape[:2]
        box_width = min(self.anchor_size[0], width)
        box_height = min(self.anchor_size[1], height)
        left = max(0, min(x - box_width // 2, width - box_width))
        top = max(0, min(y - box_height // 2, height - box_height))
        piece = crop(image, {"x": left, "y": top,
                             "width": box_width, "height": box_height})
        return Anchor(
            name=name,
            png=to_png_bytes(piece),
            dx=x - (left + box_width // 2),
            dy=y - (top + box_height // 2),
            width=box_width,
            height=box_height,
        )

    def _flush_text(self) -> None:
        typed = "".join(self._typed)
        self._typed.clear()
        if not typed:
            return
        step: dict[str, Any] = {"action": "type", "text": typed}
        if self._last_target:
            step["target"] = self._last_target
        else:
            # Typed before anything was clicked, so there is no picture of
            # where it went. It replays into whatever has focus when the run
            # starts, which is a real assumption about the application's
            # state and worth saying out loud.
            self.warnings.append(
                "typing was recorded before any click, so it replays into "
                "whatever has focus at the start. Click the box you type into "
                "first, and the flow will find it by picture."
            )
        self.steps.append(step)

    def _lift_prompt(self, steps: list[dict[str, Any]]) -> str:
        """Replace the longest typed text with the prompt variable."""
        typing = [step for step in steps if step["action"] == "type"]
        if not typing:
            return "Ask something here"
        longest = max(typing, key=lambda step: len(step["text"]))
        prompt = longest["text"]
        longest["text"] = "{{prompt}}"
        return prompt
