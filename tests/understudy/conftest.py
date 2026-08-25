import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture(autouse=True)
def isolated_subject_store(tmp_path, monkeypatch):
    """Never the developer's own ~/.understudy.

    What a flow was last run against is remembered in a file in the home
    directory, and a test that reads it is a test whose result depends on what
    the person running it did yesterday. That has now caused two failures: one
    passing on Linux because it found a real store, and one failing here
    because it found the wrong one. Both times the product was right.
    """
    import understudy.subject as subject

    monkeypatch.setattr(subject, "DEFAULT_STORE",
                        tmp_path / "home" / ".understudy" / "subjects.json")
