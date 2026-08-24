"""Editing operations on flow files that are safer done as text.

Duplicating a flow means copying it and giving the copy a new identity. Doing
that by loading the YAML and dumping it back would work and would also throw
away every comment, every bit of spacing, and the ordering the author chose --
which in a file full of pixel regions and anchor paths is most of what makes it
readable. So these are line-level rewrites of the top-level keys, leaving
everything else byte-for-byte.
"""

from __future__ import annotations

import re
from pathlib import Path

#: Only ever matched at column zero, so a `name:` nested inside `targets:` or a
#: strategy is never touched.
_TOP_LEVEL = r"^{key}:[ \t]*(?P<value>.*)$"


class AuthoringError(ValueError):
    """The edit could not be made safely."""


def set_top_level(text: str, key: str, value: str) -> str:
    """Replace a top-level scalar, or insert it after `version:` if absent."""
    quoted = _quote(value)
    pattern = re.compile(_TOP_LEVEL.format(key=re.escape(key)), re.MULTILINE)
    if pattern.search(text):
        return pattern.sub(f"{key}: {quoted}", text, count=1)

    version = re.compile(_TOP_LEVEL.format(key="version"), re.MULTILINE)
    match = version.search(text)
    if not match:
        return f"{key}: {quoted}\n{text}"
    end = match.end()
    return f"{text[:end]}\n{key}: {quoted}{text[end:]}"


def _quote(value: str) -> str:
    if value == "" or re.search(r"[:#\[\]{}&*!|>'\"%@`]|^\s|\s$", value):
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    return value


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "flow"


def duplicate_text(
    text: str, name: str, title: str | None = None, description: str | None = None
) -> str:
    """The copy's new identity. Everything else is left alone."""
    if not name.strip():
        raise AuthoringError("the copy needs a name")
    out = set_top_level(text, "name", name.strip())
    if title is not None:
        out = set_top_level(out, "title", title.strip())
    if description is not None:
        out = set_top_level(out, "description", description.strip())
    return out


def duplicate_file(
    source: Path | str,
    destination: Path | str,
    name: str | None = None,
    title: str | None = None,
    description: str | None = None,
    overwrite: bool = False,
) -> Path:
    source, destination = Path(source), Path(destination)
    if not source.exists():
        raise AuthoringError(f"no flow at {source}")
    if destination.exists() and not overwrite:
        raise AuthoringError(f"{destination} already exists")
    if source.resolve() == destination.resolve():
        raise AuthoringError("the copy needs a different path from the original")

    new_name = name or slugify(destination.stem)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        duplicate_text(
            source.read_text(encoding="utf-8"), new_name, title, description
        ),
        encoding="utf-8",
    )
    return destination
