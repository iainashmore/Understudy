"""Where the Anthropic API key lives.

Rules this file exists to enforce:

  * **The key is stored outside the project by default** -- under the user's
    home directory, not the workspace -- so it cannot be committed by accident.
    A workspace location is allowed, and `.gitignore` covers it, but the
    default is the one that is safe when someone runs `git add -A` at 6pm.
  * **The key is never sent back to the browser.** Reads return a masked form
    (`sk-ant-...4f2a`). The UI can tell you a key is saved and which one; it
    cannot show it to you, and neither can anything that screenshots the UI.
  * **The key never reaches a run directory.** Nothing here is written into
    traces, reports or results, all of which get zipped up and passed around.
  * **The file is owner-only** where the platform supports it.

An environment variable still wins. That is the convention everywhere else, and
a saved key silently overriding `ANTHROPIC_API_KEY` would be a nasty surprise
on a machine where CI or a wrapper script sets one.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path.home() / ".flowrunner" / "credentials.json"
ENV_KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
#: Anything not on this list is refused rather than quietly stored.
ALLOWED_FIELDS = ("api_key", "base_url", "model")


@dataclass
class Credentials:
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    path: Path = field(default=DEFAULT_PATH)

    @property
    def has_key(self) -> bool:
        return bool(self.api_key)

    def masked(self) -> str:
        """Enough to recognise which key it is, not enough to use it."""
        if not self.api_key:
            return ""
        if len(self.api_key) <= 12:
            return "*" * len(self.api_key)
        return f"{self.api_key[:7]}...{self.api_key[-4:]}"

    def public(self) -> dict[str, Any]:
        """The only shape that may leave the process."""
        return {
            "configured": self.has_key,
            "masked_key": self.masked(),
            "base_url": self.base_url,
            "model": self.model,
            "path": str(self.path),
            "env_override": active_env_source(),
        }


def active_env_source() -> str:
    for name in ENV_KEYS:
        if os.environ.get(name):
            return name
    return ""


def load(path: Path | str | None = None) -> Credentials:
    path = Path(path) if path else DEFAULT_PATH
    if not path.exists():
        return Credentials(path=path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return Credentials(path=path)
    return Credentials(
        api_key=str(data.get("api_key", "") or ""),
        base_url=str(data.get("base_url", "") or ""),
        model=str(data.get("model", "") or ""),
        path=path,
    )


def save(values: dict[str, Any], path: Path | str | None = None) -> Credentials:
    """Write the credentials file, owner-readable only.

    An empty or absent `api_key` clears the stored key rather than leaving a
    stale one behind -- "I deleted it in the UI" has to mean it is gone.
    """
    path = Path(path) if path else DEFAULT_PATH
    unknown = sorted(set(values) - set(ALLOWED_FIELDS))
    if unknown:
        raise ValueError(f"unknown credential field(s): {', '.join(unknown)}")

    payload = {
        field_name: str(values.get(field_name, "") or "").strip()
        for field_name in ALLOWED_FIELDS
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        # Windows and some network shares do not honour this. Not fatal, but
        # it is the reason the default location is a per-user directory.
        pass
    return load(path)


def clear(path: Path | str | None = None) -> Credentials:
    path = Path(path) if path else DEFAULT_PATH
    if path.exists():
        path.unlink()
    return Credentials(path=path)


def client_options(path: Path | str | None = None) -> dict[str, Any]:
    """Arguments for `anthropic.Anthropic(**options)`.

    Empty when an environment variable is set: the SDK reads it itself, and
    layering a saved key on top would make which one is in use unpredictable.
    """
    if active_env_source():
        return {}
    saved = load(path)
    options: dict[str, Any] = {}
    if saved.api_key:
        options["api_key"] = saved.api_key
    if saved.base_url:
        options["base_url"] = saved.base_url
    return options


def resolved_model(default: str, path: Path | str | None = None) -> str:
    saved = load(path)
    return saved.model or default


def check(path: Path | str | None = None) -> dict[str, Any]:
    """Verify the credentials actually work, as cheaply as possible.

    A model lookup rather than a message: it authenticates without generating
    anything, so testing the connection costs nothing.
    """
    source = active_env_source() or ("saved file" if load(path).has_key else "")
    if not source:
        return {"ok": False, "source": "", "error":
                "No API key. Save one below, or set ANTHROPIC_API_KEY."}
    try:
        import anthropic
    except ImportError:
        return {"ok": False, "source": source,
                "error": "the anthropic package is not installed "
                         "(pip install anthropic)"}
    try:
        client = anthropic.Anthropic(**client_options(path))
        model = resolved_model("claude-opus-5", path)
        info = client.models.retrieve(model)
        return {"ok": True, "source": source,
                "model": getattr(info, "id", model),
                "display_name": getattr(info, "display_name", "")}
    except Exception as exc:
        return {"ok": False, "source": source, "error": f"{type(exc).__name__}: {exc}"}
