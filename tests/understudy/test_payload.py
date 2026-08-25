"""Staging the binaries the installed application carries.

None of this runs on the machine that uses the build -- it runs once, on a
Windows builder -- so a mistake here does not fail anywhere until somebody
installs the result and finds a feature missing. That is how the bundle came
to ship pytesseract, a wrapper around a binary it did not ship, leaving OCR
broken on every installed copy and working on every development one.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_payload_module():
    spec = importlib.util.spec_from_file_location(
        "fetch_payload", ROOT / "packaging" / "fetch_payload.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


payload = load_payload_module()


class TestFindingAnInstalledTesseract:
    def test_path_wins(self, tmp_path):
        """A builder with it on PATH has already said which one they mean."""
        home = tmp_path / "somewhere"
        home.mkdir()
        found = payload.installed_tesseract(
            which=lambda name: str(home / "tesseract.exe"), homes=(),
        )
        assert found == home

    def test_it_falls_back_to_where_the_installers_put_it(self, tmp_path):
        home = tmp_path / "Tesseract-OCR"
        home.mkdir()
        (home / "tesseract.exe").write_bytes(b"")
        found = payload.installed_tesseract(
            which=lambda name: None, homes=(str(tmp_path / "nope"), str(home)),
        )
        assert found == home

    def test_missing_is_none_not_a_guess(self, tmp_path):
        assert payload.installed_tesseract(
            which=lambda name: None, homes=(str(tmp_path / "nothing"),),
        ) is None


class TestCopyingIt:
    def home(self, tmp_path):
        home = tmp_path / "Tesseract-OCR"
        (home / "tessdata").mkdir(parents=True)
        (home / "doc").mkdir()
        for name in ("tesseract.exe", "libtesseract-5.dll", "liblept-6.dll"):
            (home / name).write_bytes(b"binary")
        (home / "tessdata" / "eng.traineddata").write_bytes(b"x" * 100)
        (home / "doc" / "README").write_text("nobody needs this in a bundle")
        return home

    def test_it_takes_the_engine_and_the_libraries_it_needs(self, tmp_path):
        copied = payload.copy_tesseract(self.home(tmp_path), tmp_path / "tools")

        assert "tesseract.exe" in copied
        assert "libtesseract-5.dll" in copied, \
            "the executable alone will not start"
        assert (tmp_path / "tools" / "liblept-6.dll").exists()

    def test_it_leaves_the_rest_behind(self, tmp_path):
        """Its tessdata duplicates language data the payload already stages,
        and its documentation is of no use inside an application."""
        payload.copy_tesseract(self.home(tmp_path), tmp_path / "tools")

        staged = {path.name for path in (tmp_path / "tools").iterdir()}
        assert "tessdata" not in staged and "doc" not in staged

    def test_it_does_not_disturb_what_is_already_there(self, tmp_path):
        """ffmpeg is staged into the same folder, and staging is not ordered."""
        tools = tmp_path / "tools"
        tools.mkdir()
        (tools / "ffmpeg.exe").write_bytes(b"ffmpeg")

        payload.copy_tesseract(self.home(tmp_path), tools)

        assert (tools / "ffmpeg.exe").read_bytes() == b"ffmpeg"


def test_the_runtime_looks_for_the_engine_where_staging_puts_it():
    """The two halves have to agree, and they are written a year apart in
    different files: staging drops the engine in payload/tools, and the frozen
    entry point puts that folder on PATH, which is where pytesseract looks."""
    entry = (ROOT / "packaging" / "server_entry.py").read_text()
    assert 'root / "tools"' in entry
    assert 'os.environ["PATH"]' in entry
