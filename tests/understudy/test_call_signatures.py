"""Every call to the vision helpers matches the function it is calling.

Most of the code that uses these runs only on Windows -- the Notepad anchor
tool, the live native tests -- so a wrong argument list is not a failure here,
it is a failure on a runner, twenty minutes later, after the desktop has been
booted and Notepad launched.

That happened: `crop(window, x=8, y=8, width=90, height=24)` against a function
whose signature is `crop(screenshot, region)`. Four CI runs died before a flow
ran, and nothing on this side of the machine could have caught it, because the
line never executed here.

This executes nothing. It reads every call by name and asks whether the
arguments would bind, which is the part that was wrong.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from understudy import vision

ROOT = Path(__file__).resolve().parents[2]

#: Called as plain names, so a call site is unambiguous in the syntax tree.
#: `image.crop(...)` is Pillow's and is not one of these.
CHECKED = {name: getattr(vision, name) for name in ("crop", "locate", "locate_all")}

SOURCES = sorted(
    path
    for folder in ("understudy", "tools", "tests")
    for path in (ROOT / folder).rglob("*.py")
)


def call_sites():
    for path in SOURCES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in CHECKED:
                    yield path, node


def test_there_are_call_sites_to_check():
    """A test that has quietly stopped matching anything passes forever."""
    assert list(call_sites()), "found no calls at all -- the walk is broken"


@pytest.mark.parametrize(
    "path,node",
    [(p, n) for p, n in call_sites()],
    ids=[f"{p.relative_to(ROOT)}:{n.lineno}" for p, n in call_sites()],
)
def test_the_arguments_would_bind(path, node):
    if any(isinstance(a, ast.Starred) for a in node.args) or \
            any(k.arg is None for k in node.keywords):
        pytest.skip("unpacked arguments: nothing to check statically")

    signature = inspect.signature(CHECKED[node.func.id])
    try:
        signature.bind(*[None] * len(node.args),
                       **{k.arg: None for k in node.keywords})
    except TypeError as exc:
        pytest.fail(
            f"{path.relative_to(ROOT)}:{node.lineno} calls "
            f"{node.func.id}{signature} but {exc}"
        )
