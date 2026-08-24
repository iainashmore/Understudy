"""Prompt variant loading."""

from __future__ import annotations

import pytest

from flowrunner.prompts import PromptsError, load_prompts, parse_prompts

YAML = """
- id: baseline
  prompt: "Summarise this in one paragraph."
- id: terse
  prompt: "Summarise this in one sentence."
"""

CSV = "id,prompt,style\nbaseline,Summarise this.,plain\nformal,Summarise this.,formal\n"


def test_yaml_variants_load():
    prompts = parse_prompts(YAML, "yaml")
    assert [v.id for v in prompts] == ["baseline", "terse"]
    assert prompts.variants[0].prompt == "Summarise this in one paragraph."


def test_csv_variants_load_with_every_column_as_a_variable():
    prompts = parse_prompts(CSV, "csv")
    assert [v.id for v in prompts] == ["baseline", "formal"]
    assert prompts.variants[1].variables == {
        "prompt": "Summarise this.",
        "style": "formal",
    }


def test_a_csv_written_by_excel_still_parses(tmp_path):
    # Excel prepends a BOM, which otherwise turns the first column into '﻿id'
    # and looks like a missing id.
    path = tmp_path / "prompts.csv"
    path.write_bytes(b"\xef\xbb\xbf" + CSV.encode("utf-8"))
    assert [v.id for v in load_prompts(path)] == ["baseline", "formal"]


def test_the_format_follows_the_extension(tmp_path):
    (tmp_path / "p.yaml").write_text(YAML)
    (tmp_path / "p.csv").write_text(CSV)
    assert len(load_prompts(tmp_path / "p.yaml")) == 2
    assert len(load_prompts(tmp_path / "p.csv")) == 2


def test_entries_need_an_id():
    with pytest.raises(PromptsError, match="entry 1 has no 'id'"):
        parse_prompts("- prompt: hello\n", "yaml")


def test_duplicate_ids_are_rejected():
    # Two rows with the same id would collide in the output directory.
    with pytest.raises(PromptsError, match="duplicate prompt id"):
        parse_prompts("- id: a\n  prompt: x\n- id: a\n  prompt: y\n", "yaml")


def test_an_empty_file_is_an_error():
    with pytest.raises(PromptsError, match="no prompt variants"):
        parse_prompts("[]", "yaml")


def test_a_yaml_mapping_is_rejected_with_a_useful_message():
    with pytest.raises(PromptsError, match="expected a list"):
        parse_prompts("baseline: hello\n", "yaml")


class TestSelection:
    def test_only_selects_and_orders(self):
        prompts = parse_prompts(YAML, "yaml").select(["terse", "baseline"])
        assert [v.id for v in prompts] == ["terse", "baseline"]

    def test_selecting_nothing_keeps_everything(self):
        assert len(parse_prompts(YAML, "yaml").select(None)) == 2

    def test_an_unknown_id_lists_what_exists(self):
        with pytest.raises(PromptsError, match="unknown prompt id.*nope"):
            parse_prompts(YAML, "yaml").select(["nope"])


class TestVariableCoverage:
    def test_a_complete_prompts_file_passes(self):
        parse_prompts(CSV, "csv").check_provides({"prompt", "style"})

    def test_a_missing_variable_is_caught_before_the_run(self):
        """Finding out on row 40 of 50 wastes the sweep."""
        with pytest.raises(PromptsError, match="baseline: missing tone"):
            parse_prompts(CSV, "csv").check_provides({"prompt", "tone"})

    def test_the_report_names_every_offending_row(self):
        with pytest.raises(PromptsError) as caught:
            parse_prompts(CSV, "csv").check_provides({"missing"})
        assert "baseline" in str(caught.value) and "formal" in str(caught.value)
