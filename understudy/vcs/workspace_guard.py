"""Refusing to treat Understudy's own source tree as a flow workspace.

The tool is started with `--workspace .`, and the most likely `.` on the day
somebody first tries it is the checkout they cloned to get the tool. Publishing
a run there commits somebody's CAD screenshots into a source repository, and
pushing it sends them to whoever owns that repository -- which is not the person
who ran it.

That is a mistake worth making impossible rather than warning about, so writes
are refused unless the checkout says, in a file somebody had to create, that it
really is meant to hold flows.
"""

from __future__ import annotations

from pathlib import Path

#: Create this file to say "yes, flows really do live in this checkout".
MARKER = ".understudy-workspace"

#: Files that together mean "this is the tool, not somebody's flows".
_SOURCE_MARKERS = (
    Path("understudy") / "cli.py",
    Path("understudy") / "runner.py",
    Path("pyproject.toml"),
)


def is_source_checkout(root: Path | str) -> bool:
    """True when this looks like a checkout of Understudy itself."""
    root = Path(root)
    if (root / MARKER).exists():
        # Deliberately opted in. Somebody had to create this file by hand,
        # which is a high enough bar.
        return False
    return all((root / marker).exists() for marker in _SOURCE_MARKERS)


def refusal(root: Path | str) -> str:
    """Why this workspace will not be written to, and what to do instead."""
    return (
        f"{root} looks like a checkout of Understudy itself, not a flow "
        f"workspace. Committing runs here would put your screenshots into the "
        f"tool's source repository and push them wherever that points.\n\n"
        f"Point Understudy at your own repository instead -- clone it from the "
        f"Repository tab, or start with --workspace /path/to/your/flows. If "
        f"flows really do belong in this checkout, create a file called "
        f"{MARKER} in it."
    )
